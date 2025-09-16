## Accumulator

### 1、职责

Accumulator给每个分区维护了一个Batch Deque，通过Batch和Deque的状态确定是否达到发送条件，是否已经超时发送等等。



### 2、源码详解

#### 2.1、accumulator.ready

该方法用于判断是否有节点可以发消息了，返回值为Set  readyNodes。

调用链：

+ Sender.run()
+ Sender.runOnce()
+ Sender.sendProducerData()
+ accumulator.ready()

源码：

```java
RecordAccumulator {
    public ReadyCheckResult ready(Cluster cluster, long nowMs) {
        Set<Node> readyNodes = new HashSet<>();
        long nextReadyCheckDelayMs = Long.MAX_VALUE;
        Set<String> unknownLeaderTopics = new HashSet<>();
        for (Map.Entry<String, TopicInfo> topicInfoEntry : this.topicInfoMap.entrySet()) {
            final String topic = topicInfoEntry.getKey();
            nextReadyCheckDelayMs = partitionReady(cluster, nowMs, topic, topicInfoEntry.getValue(), nextReadyCheckDelayMs, readyNodes, unknownLeaderTopics);
        }
        return new ReadyCheckResult(readyNodes, nextReadyCheckDelayMs, unknownLeaderTopics);
    }
    
    private long partitionReady(Cluster cluster, long nowMs, String topic,
                                TopicInfo topicInfo,
                                long nextReadyCheckDelayMs, Set<Node> readyNodes, Set<String> unknownLeaderTopics) {
        ConcurrentMap<Integer, Deque<ProducerBatch>> batches = topicInfo.batches;
        int[] queueSizes = null;
        int[] partitionIds = null;
        if (batches.size() >= cluster.partitionsForTopic(topic).size()) {
            queueSizes = new int[batches.size()];
            partitionIds = new int[queueSizes.length];
        }

        for (Map.Entry<Integer, Deque<ProducerBatch>> entry : batches.entrySet()) {
            TopicPartition part = new TopicPartition(topic, entry.getKey());
            Node leader = cluster.leaderFor(part);
            Deque<ProducerBatch> deque = entry.getValue();
            synchronized (deque) {
                ProducerBatch batch = deque.peekFirst();
                if (batch == null) {
                    continue;
                }

                waitedTimeMs = batch.waitedTimeMs(nowMs);
                backingOff = batch.attempts() > 0 && waitedTimeMs < retryBackoffMs;
                dequeSize = deque.size();
                full = dequeSize > 1 || batch.isFull();
            }

            if (leader == null) {
                unknownLeaderTopics.add(part.topic());
            } else {
                nextReadyCheckDelayMs = batchReady(nowMs, exhausted, part, leader, waitedTimeMs, backingOff,
                    full, nextReadyCheckDelayMs, readyNodes);
            }
        }

        return nextReadyCheckDelayMs;
    }
    
        private long batchReady(long nowMs, boolean exhausted, TopicPartition part, Node leader,
                            long waitedTimeMs, boolean backingOff, boolean full,
                            long nextReadyCheckDelayMs, Set<Node> readyNodes) {
        if (!readyNodes.contains(leader) && !isMuted(part)) {
            long timeToWaitMs = backingOff ? retryBackoffMs : lingerMs;
            boolean expired = waitedTimeMs >= timeToWaitMs;
            boolean transactionCompleting = transactionManager != null && transactionManager.isCompleting();
            boolean sendable = full
                    || expired
                    || exhausted
                    || closed
                    || flushInProgress()
                    || transactionCompleting;
            if (sendable && !backingOff) {
                readyNodes.add(leader);
            } else {
                long timeLeftMs = Math.max(timeToWaitMs - waitedTimeMs, 0);
                nextReadyCheckDelayMs = Math.min(timeLeftMs, nextReadyCheckDelayMs);
            }
        }
        return nextReadyCheckDelayMs;
    }
}
```

topic.batches是一个Map<Integer, Deque>，表示每个分区维护了一个batch的先进先出队列，判断分区是否可以发送的条件（batchReady）满足以下任意一个即可：

+ 队列存在一个以上的Batch（说明之前有Batch满了才会创建新的）
+ 只有一个Batch且Batch刚好满了
+ 等待时间超了linger.ms
+ 重试的时候等待间隔超了retry.backoff.ms
+ buffer.memory满了
+ 主动调用过producer.flush()

条件满足后会将这个分区的Leader加到readyNodes里面。



#### 2.2、accumulator.drain

源码：

```
	public Map<Integer, List<ProducerBatch>> drain(Cluster cluster, Set<Node> nodes, int maxSize, long now) {
        if (nodes.isEmpty())
            return Collections.emptyMap();

        Map<Integer, List<ProducerBatch>> batches = new HashMap<>();
        for (Node node : nodes) {
            List<ProducerBatch> ready = drainBatchesForOneNode(cluster, node, maxSize, now);
            batches.put(node.id(), ready);
        }
        return batches;
    }
    
    private List<ProducerBatch> drainBatchesForOneNode(Cluster cluster, Node node, int maxSize, long now) {
        int size = 0;
        List<PartitionInfo> parts = cluster.partitionsForNode(node.id());
        List<ProducerBatch> ready = new ArrayList<>();
        int drainIndex = getDrainIndex(node.idString());
        int start = drainIndex = drainIndex % parts.size();
        do {
            PartitionInfo part = parts.get(drainIndex);
            TopicPartition tp = new TopicPartition(part.topic(), part.partition());
            updateDrainIndex(node.idString(), drainIndex);
            drainIndex = (drainIndex + 1) % parts.size();
            if (isMuted(tp))
                continue;

            Deque<ProducerBatch> deque = getDeque(tp);
            if (deque == null)
                continue;

            final ProducerBatch batch;
            synchronized (deque) {
                // invariant: !isMuted(tp,now) && deque != null
                ProducerBatch first = deque.peekFirst();
                if (first == null)
                    continue;

                // first != null
                boolean backoff = first.attempts() > 0 && first.waitedTimeMs(now) < retryBackoffMs;
                // Only drain the batch if it is not during backoff period.
                if (backoff)
                    continue;

                if (size + first.estimatedSizeInBytes() > maxSize && !ready.isEmpty()) {
                    break;
                } else {
                    if (shouldStopDrainBatchesForPartition(first, tp))
                        break;
                }

                batch = deque.pollFirst();
            }

            // the rest of the work by processing outside the lock
            // close() is particularly expensive

            batch.close();
            size += batch.records().sizeInBytes();
            ready.add(batch);

            batch.drained(now);
        } while (start != drainIndex);
        return ready;
    }
```



accumulator.ready()得到的readyNodes后，遍历readyNodes，通过cluster.partitionsForNode(nodeId)拿到节点的分区列表，每个分区都遍历到，从分区DequeBatch中取出Batch加到readyBatchList返回。中途如果获取的BatchSize总和已经超过了max.request.size则直接返回，不用再往下遍历了。

这里注意一个点：每次开始轮询的分区要从上一次结束的地方开始（drainIndex），避免每次都从0开始导致后面的分区遍历不到。



#### 2.3、accumulator.expired

从Kafka2.1.0开始引入了deliever.timeout.ms的概念，允许控制消息发送重试的时间。每个Batch创建时都记录了createdMs，这里检查 now() - createdMs >= deliever.timeout.ms，如果超时了需要将这个Batch从Deque出队，打印日志Expired xx records(s) for topic partition after xx ms has passed since batch creation。

当出现batch超时情况时，需要检查消息是否还在正常发，如果消息能成功发送成功说明只是发送慢了，可能是客户端存在发送瓶颈（公网带宽不足，生成消息过多）或者服务端发送请求耗时高（磁盘瓶颈）。