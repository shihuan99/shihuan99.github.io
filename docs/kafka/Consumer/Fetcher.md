## Fetcher

### 1、职责

位点管理（检查当前消费位点是否合法、是否需要重置位点）

数据拉取



### 2、源码详解

#### 2.1、位点管理

##### updateFetchPositions(final Timer timer)

目的是将所订阅的Topic分区的消费位点重置到commited position，或者如果找不到位点的话需要根据auto.offset.reset策略重置。

调用链：
+ KafkaConsumer.poll(Timer timer, boolean includeMetadataInTimeout)
+ updateAssignmentMetadataIfNeeded(final Timer timer, final boolean waitForJoinGroup)
+ updateFetchPositions(final Timer timer)
+ validateOffsetsIfNeeded()
+ maybeCompleteValidation()

validateOffsetsIfNeeded()方法先检查当前消费位点是否合法，如果位点所在消息不存在且配置了reset policy，则需要自动重置位点，有两种情况：

+ 当前分区消费位点大于endOffset，重置到endOffset（指定offset）
+ 其它情况根据reset policy重置到最新或者最老的消息位置（LATEST、EARLIEST）

位点重置不是同步进行的，而是把这个分区的SubscriptionState的状态标记成AWAIT_FETCH，后面的resetOffsetsIfNeeded()会检查哪些分区需要reset。

SubscriptionState的好处是通过分区状态将事件归一，当这个事件有多个来源时，可以避免执行多次。比如validateOffset可能触发重置、OffsetFetchResponse也可能触发重置、以及手动触发的seekingToOffset等。SubScriptionState是一个状态机，有多个状态：INITIALIZING、FETCHIN、AWAIT_RESET、AWAIT_VALIDATION。



#### 2.2、数据拉取

调用链：
+ KafkaConsumer.poll(Timer timer, boolean includeMetadataInTimeout)
+ Fetcher.pollForFetches(timer)
+ fetcher.collectFetch() 收集之前fetch的结果，如果有消息则直接返回，没消息则往下走
+ Fetcher.sendFetches() 根据SubscriptionState.subscriptions的分区Leader构造各个节点的FETCH请求，每个broker一个请求
+ client.send(node, request)
+ completedFetches.add(new CompletedFetch(..)) 请求成功后往completeFetches里面加，之前的collectFetch就是从这里读的。因为整个流程是异步进行的，并不会在发送了请求后一直等它响应，所以就需要找个地方存下来异步响应结果，定期取出来。

##### Fetch<K, V> collectFetch()

由于一次性从多个broker拉取消息，completedFetches就会有多个，过程中会设置nextInLineFetch=completedFetches.peek()，遍历completeFetches直到拿到max.poll.records的消息就返回。

从completedFetch读出来消息后，会调用SubscriptionState.position(completedFetch.partition,  completedFetch.nextFetchOffset)去更新下次拉取的消息位置。

##### Fetcher.sendFetches()

通过fetchablePartitions()方法判断哪些分区需要发送请求，如果分区的消息即将被消费（nextInLineFetch或者completedFetches存在这个分区），因为这个分区之前拉取下来的消息还没有被消费，fetchOffset还没变，如果这个时候去Fetch就相当于是拉了重复消息回来。

##### FetchSessionHandler && Incremental Fetch

每个broker会创建一个FetchSessionHandler，里面维护了一个FetchRequestData()，包括维护这个节点要拉取哪些分区、拉取的Offsets。

分区是第一次Fetch时会add进分区列表；如果不想消费这个分区了，会remove掉。在发送给Kafka的FetchRequest中会携带added、removed等增量变动信息，保持Kafka记录的fetchSession和消费者一样。后续只要订阅的分区信息没有变动，就不用再发这些Topic分区信息了，减小FETCH请求大小。

##### initializeCompletedFetch  handleErrror

针对Kafka返回的错误码，及时调用requestMetadataUpdate更正自己的元数据信息。

如果时OffsetOutOfRange错误，需要根据auto.offset.reset重置位点，如果没有配置重置策略则抛异常。

