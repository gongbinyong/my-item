
from celery import Celery
import os
# 配置celery如果需要用配置文件时去那里加载
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meiduo.settings.dev")


# 1.创建celery客户端
celery_app = Celery('meiduo')

# 2.加载配置信息
celery_app.config_from_object('celery_tasks.config')
# 3.注册异步任务(那些任务可以进入到任务队列)
celery_app.autodiscover_tasks(['celery_tasks.sms', 'celery_tasks.email', 'celery_tasks.html'])