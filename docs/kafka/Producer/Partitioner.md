## Partitioner

### 1、职责

Producer.send(record)后需要选择分区，除非指定了分区，否则需要由Partitioner来自动选择分区，通过配置partitioner.class可以选择不同的分区器。



### 2、源码详解

Kafka预置了几个Partitioner(DefaltPartitioner, RoundRobinParitioner)，用户也可以自己定义，需要实现几个方法：

+ int partition(topic, key)计算分区
+ void close()
+ void onNewBatch(topic, cluster, prevPartition) batch满了，需要在这里选择新分区

#### 2.1、DefaultPartitioner

在低版本默认用的是RoundRobinPartitioner，高版本考虑到RoundRobin会造成请求碎片问题，改成了默认使用Sticky，目的是尽量写满同个Batch再切分区。

源码：

```java
public class DefaultPartitioner implements Partitioner {
    private final StickyPartitionCache stickyPartitionCache = new StickyPartitionCache();

    public int partition(String topic, Object key, byte[] keyBytes, Object value, byte[] valueBytes, Cluster cluster) {
        return partition(topic, key, keyBytes, value, valueBytes, cluster, cluster.partitionsForTopic(topic).size());
    }

    public int partition(String topic, Object key, byte[] keyBytes, Object value, byte[] valueBytes, Cluster cluster,
                         int numPartitions) {
        if (keyBytes == null) {
            return stickyPartitionCache.partition(topic, cluster);
        }
        return BuiltInPartitioner.partitionForKey(keyBytes, numPartitions);
    }

    @SuppressWarnings("deprecation")
    public void onNewBatch(String topic, Cluster cluster, int prevPartition) {
        stickyPartitionCache.nextPartition(topic, cluster, prevPartition);
    }
}
```

如果指定了key，则通过key去hash算分区。如果没指定key，则返回上一次选出的分区，直到新batch创建才会切换分区。

**Question:** 为什么有时候会看到消息都往一个分区写？

**Answer:** 在batch.size很大或者消息很小的时候，由于没达到batch切换条件，在linger.ms内写入的消息都会进到同个分区batch。

#### 2.2、RoundRobinPartitioner

RoundRobin是绝对的轮询选择分区，一条消息换一个分区，容易造成batch碎片化问题。源码：

```
public class RoundRobinPartitioner implements Partitioner {
    private final ConcurrentMap<String, AtomicInteger> topicCounterMap = new ConcurrentHashMap<>();

    @Override
    public int partition(String topic, Object key, byte[] keyBytes, Object value, byte[] valueBytes, Cluster cluster) {
        List<PartitionInfo> partitions = cluster.partitionsForTopic(topic);
        int numPartitions = partitions.size();
        int nextValue = nextValue(topic);
        List<PartitionInfo> availablePartitions = cluster.availablePartitionsForTopic(topic);
        if (!availablePartitions.isEmpty()) {
            int part = Utils.toPositive(nextValue) % availablePartitions.size();
            return availablePartitions.get(part).partition();
        } else {
            return Utils.toPositive(nextValue) % numPartitions;
        }
    }

    private int nextValue(String topic) {
        AtomicInteger counter = topicCounterMap.computeIfAbsent(topic, k -> new AtomicInteger(0));
        return counter.getAndIncrement();
    }
}
```

