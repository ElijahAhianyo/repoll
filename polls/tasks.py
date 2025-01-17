import time
import redis
from polls.models import RunningInsTime, RunningInsSentinel, RunningInsStandalone, RunningInsCluster, RealTimeQps
from django.utils import timezone
from polls.scheduled import RedisScheduled
import threading
import logging

# 日志格式
logger = logging.getLogger("redis.monitor")


def get_redis_status(func, ins_name):
    """
    获取当前redis实例或集群的状态，判断是否需要执行入库操作
    :param func:
    :param ins_name:
    :return:
    """
    running_type = func.objects.filter(running_ins_name=ins_name).values("running_time")
    return running_type["running_time"]


def set_redis_status(func, ins_name, mes):
    """
    获取当前redis实例或集群的状态，判断是否需要执行入库操作
    :param func:
    :param ins_name:
    :param mes:
    :return:
    """
    try:
        func.objects.filter(running_ins_name=ins_name).update(running_time=mes)
    except ImportError as e:
        logger.error(e)
    return True


def get_all_redis_ins():
    """
    获取当前平台内所有的redis实例的ip和端口，用于监控使用
    :return: 所有redis实例的ip和端口的列表
    :type: list
    """
    running_ins_names = RunningInsTime.objects.all()
    all_redis_names = [running_ins_name.__dict__['running_ins_name'] for running_ins_name in running_ins_names]
    all_ip_port = []    # 平台内所有入库的redis 实例的ip地址和端口存入该列表
    all_sentienl_ip_port = []   # 平台内所有哨兵实例的ip地址和端口存入该列表
    for redis_name in all_redis_names:
        try:
            redis_ins = running_ins_names.get(running_ins_name=redis_name)
            if redis_ins.redis_type == 'Redis-Sentinel':
                redis_sentinel = RunningInsSentinel.objects.all().filter(running_ins_name=redis_name).values()
                for redis_ip_port in redis_sentinel:
                    ip_port = {}
                    if redis_ip_port["redis_type"] != 'Redis-Sentinel':
                        ip_port['redis_ip'] = redis_ip_port['redis_ip']
                        ip_port['running_ins_port'] = redis_ip_port['running_ins_port']
                        ip_port['redis_ins_mem'] = redis_ip_port['redis_ins_mem']
                        ip_port['redis_ins'] = redis_ins
                        ip_port['redis_type'] = 'Redis-Sentinel'
                        all_ip_port.append(ip_port)
                    else:
                        ip_port['redis_ip'] = redis_ip_port['redis_ip']
                        ip_port['running_ins_port'] = redis_ip_port['running_ins_port']
                        all_sentienl_ip_port.append(ip_port)
            elif redis_ins.redis_type == 'Redis-Standalone':
                redis_standalone = RunningInsStandalone.objects.all().filter(running_ins_name=redis_name).values()
                for redis_standalone_ins in redis_standalone:
                    ip_port = {}
                    ip_port['redis_ip'] = redis_standalone_ins['redis_ip']
                    ip_port['running_ins_port'] = redis_standalone_ins['running_ins_port']
                    ip_port['redis_ins_mem'] = redis_standalone_ins['redis_ins_mem']
                    ip_port['redis_ins'] = redis_ins
                    ip_port['redis_type'] = 'Redis-Standalone'
                    all_ip_port.append(ip_port)
            elif redis_ins.redis_type == 'Redis-Cluster':
                redis_cluster = RunningInsCluster.objects.all().filter(running_ins_name=redis_name).values()
                for redis_ip_port in redis_cluster:
                    ip_port = {}
                    ip_port['redis_ip'] = redis_ip_port['redis_ip']
                    ip_port['running_ins_port'] = redis_ip_port['running_ins_port']
                    ip_port['redis_ins_mem'] = redis_ip_port['redis_ins_mem']
                    ip_port['redis_ins'] = redis_ins
                    ip_port['redis_type'] = 'Redis-Cluster'
                    all_ip_port.append(ip_port)
        except redis.exceptions.ConnectionError as e:
            logger.error("报错信息为".format(e))
        logger.error("all_sentienl_ip_port : {0}".format(all_sentienl_ip_port))
    return all_ip_port, all_sentienl_ip_port


def get_redis_ins_qps():
    """
    Redis ins 所有的redis实例进行监控
    TODO: 哨兵模式和集群模式的redis 角色变化监控，速率为现状是分钟级，需要精确到秒级。有优化方案的大佬请pull request万分感激。
    :return:
    """
    all_ip_port, all_sentienl_ip_port = get_all_redis_ins()
    logger.error("所有redis实例详情:{0}\n所有redis哨兵详情:{1}".format(all_ip_port, all_sentienl_ip_port))
    for items in all_ip_port:
        try:
            redis_mon = RedisScheduled(redis_ip=items['redis_ip'], redis_port=items['running_ins_port'],
                                       redis_ins_mem=items['redis_ins_mem'], redis_ins=items['redis_ins'])
            if not redis_mon.redis_alive:
                if items["redis_type"] == 'Redis-Cluster':
                    now_status = get_redis_status(RunningInsCluster, items['redis_ins'].running_ins_name)
                    if now_status != "未启动":
                        set_redis_status(RunningInsCluster, items['redis_ins'].running_ins_name, "未启动")
                    cluster_alive_status = redis_mon.cluster_alive_status
                    if not cluster_alive_status or cluster_alive_status["cluster_state"] != "ok":
                        RunningInsTime.objects.filter(running_ins_name=items['redis_ins'].running_ins_name).update(
                            running_time=0, running_type="未运行")
                        logger.error("集群实例{0}，未启动".format(items['redis_ins'].running_ins_name))
                elif items["redis_type"] == 'Redis-Sentinel':
                    RunningInsSentinel.objects.filter(redis_ip=items['redis_ip'],
                                                      running_ins_port=items['running_ins_port']).update(
                        redis_ins_alive="未启动")
                    for sentienl_items in all_sentienl_ip_port:
                        redis_sentienl_mon = RedisScheduled(redis_ip=sentienl_items['redis_ip'],
                                                            redis_port=sentienl_items['running_ins_port'])
                        if not redis_sentienl_mon.redis_alive:
                            RunningInsTime.objects.filter(running_ins_name=items['redis_ins'].running_ins_name).update(
                                running_time=0, running_type="未运行")
                            logger.error("========哨兵实例{0} 未运行======".format(items['redis_ins'].running_ins_name))
                        else:
                            if redis_sentienl_mon.info:
                                if redis_sentienl_mon.info["master0"]["status"] != "ok":
                                    RunningInsTime.objects.filter(
                                        running_ins_name=items['redis_ins'].running_ins_name).update(
                                        running_time=0, running_type="未运行")
                                    logger.error("哨兵实例{0}， 未启动".format(items['redis_ins'].running_ins_name))
                else:
                    RunningInsStandalone.objects.filter(redis_ip=items['redis_ip'],
                                                        running_ins_port=items['running_ins_port']).update(
                        redis_ins_alive="未启动")
                    RunningInsTime.objects.filter(running_ins_name=items['redis_ins'].running_ins_name).update(
                        running_time=0, running_type="未运行")
                logger.error("实例{0}监控异常, {1}::::{2}".format(items['redis_ins'], items['redis_ip'], items['running_ins_port']))
            else:
                redis_memory_usage = redis_mon.redis_memory_usage()
                ops = redis_mon.ops()
                redis_running_type = redis_mon.redis_running_type()
                redis_used_memory_human = redis_mon.redis_used_memory_human()
                redis_uptime_in_days = redis_mon.redis_uptime_in_days()
                if int(redis_uptime_in_days) == 0:
                    redis_uptime_in_days = 1
                real_time_qps_obj = RealTimeQps(redis_used_mem=redis_used_memory_human,
                                                redis_qps=ops,
                                                redis_ins_used_mem=redis_memory_usage,
                                                collect_date=timezone.now,
                                                redis_running_monitor=items['redis_ins'],
                                                redis_ip=items['redis_ip'],
                                                redis_port=items['running_ins_port'],
                                                running_type="运行中")
                real_time_qps_obj.save()
                if items["redis_type"] == 'Redis-Cluster':
                    cluster_alive_status = redis_mon.cluster_alive_status
                    if cluster_alive_status and cluster_alive_status["cluster_state"] == "ok":
                        RunningInsTime.objects.filter(running_ins_name=items['redis_ins'].running_ins_name).update(
                            running_type="运行中",
                            running_ins_used_mem_rate=redis_memory_usage,
                            running_time=redis_uptime_in_days)
                    RunningInsCluster.objects.filter(redis_ip=items['redis_ip'],
                                                     running_ins_port=items['running_ins_port']).update(
                        redis_type=redis_running_type,
                        redis_ins_alive="运行中")
                    logger.info("当前实例{1}:{2}的角色是{0}".format(redis_running_type, items['redis_ip'], items['running_ins_port']))
                elif items["redis_type"] == 'Redis-Sentinel':
                    RunningInsSentinel.objects.filter(redis_ip=items['redis_ip'],
                                                      running_ins_port=items['running_ins_port']).update(
                        redis_type=redis_running_type,
                        redis_ins_alive="运行中")
                    for sentienl_items in all_sentienl_ip_port:
                        redis_sentienl_mon = RedisScheduled(redis_ip=sentienl_items['redis_ip'],
                                                            redis_port=sentienl_items['running_ins_port'])
                        if redis_sentienl_mon.redis_alive:
                            result = RunningInsSentinel.objects.filter(redis_ip=sentienl_items['redis_ip'],
                                                              running_ins_port=sentienl_items['running_ins_port']).update(
                                redis_ins_alive="运行中")
                            if redis_sentienl_mon.info and redis_sentienl_mon.info["master0"]["status"] == "ok":
                                RunningInsTime.objects.filter(
                                    running_ins_name=items['redis_ins'].running_ins_name).update(
                                    running_time=redis_uptime_in_days, running_type="运行中")
                                if redis_sentienl_mon.info["master0"]["address"] == "{0}:{1}".format(items['redis_ip'], items['running_ins_port']):
                                    RunningInsTime.objects.filter(
                                        running_ins_name=items['redis_ins'].running_ins_name).update(
                                        running_ins_used_mem_rate=redis_memory_usage)
                            logger.info("{0}:{1} 当前存活状态{2},入库状态{3}".format(sentienl_items['redis_ip'],
                                                                           sentienl_items['running_ins_port'],
                                                                           redis_sentienl_mon.redis_alive, result))
                else:
                    RunningInsStandalone.objects.filter(redis_ip=items['redis_ip'],
                                                        running_ins_port=items['running_ins_port']).update(
                        redis_type=redis_running_type,
                        redis_ins_alive="运行中")
                    RunningInsTime.objects.filter(running_ins_name=items['redis_ins'].running_ins_name).update(
                        running_time=redis_uptime_in_days,
                        running_type="运行中",
                        running_ins_used_mem_rate=redis_memory_usage)
                    # logger.error("实例：{0}，{1}:{2}".format("RunningInsStandalone", items['redis_ip'], items['running_ins_port']))
        except ValueError as e:
            logger.error("报错信息为{0}".format(e))
        finally:
            pass


t = threading.Thread(target=get_redis_ins_qps, name='get_redis_ins_qps')
t.start()
t.join()
