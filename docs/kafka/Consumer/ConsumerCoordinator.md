## ConsumerCoordinator

> 本文需结合Kafka 3.4.0版本源码阅读，通过对代码核心链路的梳理，能够对Kafka消费组重平衡、位点提交机制有基本理解。

### 1、职责

消费组状态维护

消费位点提交

元数据监听



### 2、源码详解

#### 2.1、消费组状态维护

##### 2.1.1、JoinGroupIfNeeded

调用链：
+ KafkaConsumer.poll() 
+ KafkaConsumer.updateAssignmentMetadataIfNeeded() 
+ ConsumerCoordinator.poll(Timer timer, boolean waitForJoinGroup)
+ ConsumerCoordinator.ensureActiveGroup(Timer timer)
+ AbstractCoordinator.joinGroupIfNeed(Timer timer)

###### ConsumerCoordinator.poll(Timer timer, boolean waitForJoinGroup)

+ 首先会判断是否为subsribe订阅方式，如果是subscribe说明需要消费组管理，需要进行消费组重平衡
+ 对于之前已经入组过的消费者来说，需要检查heartbeatThread是否正常，如果失败了则抛出异常
+ 检查coordinator节点是否正常，先FindGroupCoordinator，然后检查和它的连接
+ 判断是否需要rejoin（刚启动、元数据有变化、订阅列表有变化），以及是否有正在进行的join操作
+ （可选）如果订阅方式是模糊匹配，则需要在这个时候去查询所有topic，筛选符合条件的topic
+ 调用ensureActiveGroup(Timer timer)触发JOIN_GROUP，如果waitForJoinGroup是true则一直卡住直到重平衡结束

###### ConsumerCoordinator.ensureActiveGroup(Timer timer)

+ 首先确保Coordinator连通
+ 对于刚启动的消费者，开启心跳线程（注意心跳线程是独立的，并且只会在COMPLETING_REBALANCE和STABLE状态才会定期发送，后面会单独介绍HeartbeatThread模块）
+ 调用AbstractCoordinator.joinGroupIfNeed(Timer timer)

###### AbstractCoordinator.joinGroupIfNeed(Timer timer)

+ 首先进入onJoinPrepare()，主要是等待正在进行的async commit完成

+ 然后执行initiateJoinGroup()，把状态置为PREPARING_REBALANCE，发送JoinGroupRequest。阅读代码时注意，由于是异步发送请求，往往会有responseHandler，这里的handler为JoinGroupResponseHandler。

  + 对于DynamicGroup来说，JoinGroupResponseHandler在处理JoinGroup响应时，第一次入组失败会得到MEMBER_ID_REQUIRED报错（服务端会在请求响应中携带memberId），消费者把这个memberId在下次joinGroup时带过去。如果JoinGroup成功了，消费者状态从PREPARING_REBALANCE改成COMPLETING_REBALANCE，并且调用heartbeat.enable()开启心跳（一旦开启了心跳，后续就能够从心跳去判断消费组是否进入rebalance）。

  + 如果JoinGroupResponse.isLeader()，那么这个消费者将作为Leader，在onLeaderElected()中生成groupAssignment，并且构造SyncGroupRequest把groupAssignment带到Kafka。其它消费者作为Follower，只需要发携带空assignment的SyncGroupRequest过去。

  + sendSyncGroupRequest的handler为SyncGroupResponseHandler，如果SYNC_GROUP没有报错，则将消费者状态改成STABLE，从response中取出assignment放入future。这里的future是通过compse和chain一层一层往上传，最终作为initiateJoinGroup的返回值。也就是initiateJoinGroup包括了JOIN_GROUP和SYNC_GROUP两个步骤，最终返回assignment。

+ 以上完成了initiateJoinGroup，进入onJoinComplete(generation, memberId, assignmentStrategy, assignment)，该方法实现了SubscriptionState subscriptions的更新，后续KafkaConsumer发送fetch时就是根据subscriptions里面的assigment去发送的。

+ 所有消费者在JoinGroup后都会发SyncGroup到Kafka，但是Follower的SyncGroup请求会进入等待，Kafka收到Leader的SyncGroup后保存groupMetadata到__consumer_offsets，进入Stable状态。调用setAndPropagateAssignment()异步唤醒Follower消费者的SyncGroup请求callback，把assignment给他们返回去。至此，Rebalance顺利结束，各消费者收到自己的SyncGroup响应后，按照assignment去持续pollFetches消费数据。

**Question：** 有时候发现消费组一直进入不了STABLE，每次都是5秒又开始PrepareRebalance，可能是什么原因？

**Answer：** Kafka收到LeaderConsumer的SYNC_GROUP请求后，需要调用groupManager.storeGroup(group, assignment)，把消费组分配方案存到__consumer_offsets中，如果这个Topic出问题（比如副本同步停止），就会导致写入失败，超时时间收到OffsetCommitRequestTimeout=5000ms控制（OFFSET_COMMIT超时5秒的原因也是类似的）。遇到该问题请检查内部Topic状态，必要时可以通过重置controller恢复。

##### 2.1.2、Heartbeat pollTimeoutExpired

每次KafkaConsumer.poll()都会调用ConsumerCoordinator.poll()，检查是否需要rejoin、刷新heartbeat.poll()记录的时间。当超过max.poll.interval.ms的时间间隔没有调用poll()时，HeartBeatThread就会检查到超时，将调用maybeLeaveGroup()，发送LeaveGroupRequest到Kafka，引发消费组rebalance。

注意这个时候消费者并不会抛异常，而是触发LeaveGroup（Kafka会将它从memberList移除），这个消费者状态重置成UNJOINED，UNJOINED状态下不会发送心跳。只要消费逻辑处理完，开始执行下一次poll()，触发joinGroupIfNeeded，这个消费者又正常加入组开始消费。

**Question：** 通常rebalance应该很快就会结束，什么场景下会导致一次Rebalance长时间无法结束？

**Answer：** 有个消费者消费时卡住，这时候有新消费者加入（比如应用重启）触发了rebalance，由于消费逻辑卡住的那个消费者没能力调用poll()触发rejoin，导致因为它迟迟结束不了rebalance，直到rebalance.timeout.ms（默认等于max.poll.interval.ms，5分钟）超时后主动将这个消费者踢掉。因此如果消费者消费处理时间长、max.poll.interval.ms设置很大，就会长时间无法结束rebalance。

##### 2.1.3、Heartbeat RebalanceInProgress

消费者成功加入组后，HeartbeatThread会定时调用sendHeartbeatRequest()发送HEARTBEAT请求到Coordinator，HeartbeatResponseHandler在发现响应报错Errors.REBALANCE_IN_PROGRESS时会调用requestRejoin()。

requestRejoin()标记AbstractCoordinator.rejoinNeeded=true，下次Coordinator.poll()时就会触发消费者joinGroup，这样每个消费者就能感知到rebalance的发生，配合Kafka完成Rebalance。

除了HeartbeatError以外，OffsetCommitResponse的Error.REBALANCE_IN_PROGRESS也能触发requestRejoin()。



#### 2.2、消费位点自动提交

调用链：
+ KafkaConsumer.poll() 
+ KafkaConsumer.updateAssignmentMetadataIfNeeded() 
+ ConsumerCoordinator.poll(Timer timer, boolean waitForJoinGroup)
+ ConsumerCoordinator.maybeAutoCommitOffsetsAsync(long now)
+ ConsumerCoordinator.autoCommitOffsetsAsync()

###### ConsumerCoordinator.autoCommitOffsetsAsync()

+ 先从(SubscriptionState) subscriptions.allConsumed()获取消费进度，逻辑是assignment维护了分区状态，分区状态中包含了offset。

+ 这些offset一开始由KafkaConsumer.updateAssignmentMetadataIfNeeded()调用updateFetchPositions()完成初始化，该方法会从Kafka获取__consumer_offsets保存的进度。后续每次Fetcher.fetchRecords()返回结果后都会记录offset，用于位点提交以及下次拉取位置。

  

#### 2.3、元数据监听变化

消费者感知Topic新增分区、分区Leader变化的方式主要是：

1、Fetch请求报错的错误码（NOT_LEADER_OR_FOLLOWER）

2、metadata.max.age定时元数据过期

触发metadata更新后，ConsumerCoordinator.rejoinNeededOrPending()会判断各订阅topic的分区数量是否一致，如果不一致则调用requestRejoin发起重平衡。



### 3、知识小结

+ 消费者创建后第一次poll()会触发JOIN_GROUP/SYNC_GROUP，完成rebalance后进入STABLE状态才可以正常消费。在此之后每次poll()都会检查下rejoinIfNeeded的flag，判断是否需要触发rejoin。
+ 心跳在COMPLETE_REBALANCE、STABLE状态下才会发
+ 消费者被动感知服务端发生rebalance的方式是通过心跳报错或者位点提交报错的错误码
+ max.poll.interval.ms监测机制是心跳线程在维护，poll间隔超时会触发LeaveGroup，标记为UNJOINED，消费者并不会close，等消息处理完下一次poll()又会触发rejoin