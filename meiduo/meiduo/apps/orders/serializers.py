from decimal import Decimal

from django.db import transaction
from django.utils import timezone
import datetime
from django.utils.timezone import utc
from django_redis import get_redis_connection
from rest_framework import serializers

from goods.models import SKU
from .models import OrderInfo, OrderGoods


class CartSKUSerializer(serializers.ModelSerializer):
    count = serializers.IntegerField(label='数量')

    class Meta:
        model = SKU
        fields = ['id', 'name', 'default_image_url', 'price', 'count']


class OrderSettlementSerializer(serializers.Serializer):
    """
    订单结算数据序列化器
    """
    freight = serializers.DecimalField(label='运费', max_digits=10, decimal_places=2)
    skus = CartSKUSerializer(many=True)


class SKUSerializer(serializers.ModelSerializer):

    class Meta:
        model = SKU
        fields = ['id', 'name', 'default_image_url', 'price']


class OrderGoodSerializer(serializers.ModelSerializer):
    sku = SKUSerializer()

    class Meta:
        model = OrderGoods
        fields = ['sku', 'count', 'price']


class CommentsSerializer(serializers.ModelSerializer):
    """获取商品评论序列化器"""
    username = serializers.CharField(label="用户名", read_only=True)

    class Meta:
        model = OrderGoods
        fields = ['username', 'comment', 'score']


class UpdateOrderGoodSerializer(serializers.Serializer):
    """保存评论序列化器"""
    comment = serializers.CharField(label='评论内容', required=True)
    score = serializers.IntegerField(label='商品评分', required=True)
    is_anonymous = serializers.BooleanField(label='是否匿名', required=False)

    def update(self, instance, validated_data):
        # 开启一个事务
        with transaction.atomic():
            # 创建事务保存点
            save_point = transaction.savepoint()
            try:
                instance.comment = validated_data.get('comment', instance.comment)
                instance.is_anonymous = validated_data.get('is_anonymous', instance.is_anonymous)
                instance.score = validated_data.get('score', instance.score)
                instance.is_commented = True
                order = OrderInfo.objects.get(order_id=instance.order_id)
                sku = SKU.objects.get(id=instance.sku_id)
                sku.comments += 1
                instance.save()
                # order.save()
                sku.save()
            except Exception:
                # 暴力回滚,无论中间出现什么问题全部回滚
                transaction.savepoint_rollback(save_point)
                raise
            else:
                transaction.savepoint_commit(save_point)
                ordergoods = OrderGoods.objects.filter(order_id=instance.order_id)
                for ordergood in  ordergoods:
                    if not ordergood.is_commented:
                        break
                else:
                    order.status = 5
                    order.save()
        return instance


class GetOrdersSerializer(serializers.ModelSerializer):
    """
    获取全部订单序列化器
    """
    skus = OrderGoodSerializer(many=True)

    class Meta:
        model = OrderInfo

        fields = ['order_id', 'create_time', 'total_amount', 'pay_method', 'status', 'skus', 'freight']


class CommitOrderSerializer(serializers.ModelSerializer):
    """保存订单序列化器"""

    class Meta:
        model = OrderInfo
        fields = ['order_id', 'pay_method', 'address']
        read_only_fields = ['order_id']
        extra_kwargs = {
            'address': {
                'write_only': True,
                'required': True,
            },
            'pay_method': {
                'write_only': True,
                'required': True
            }
        }

    def create(self, validated_data):
        user = self.context['request'].user
        # 生成订单编号 当前时间 + user_id  20190215100800000000001
        order_id = timezone.now().strftime('%Y%m%d%H%M%S') + "%09d" % user.id
        # 获取用户选择的收货地址
        address = validated_data.get('address')
        # 获取支付方式
        pay_method = validated_data.get('pay_method')

        # 订单状态: 如果用户选择的是货到付款, 订单应该是待发货  如果用户选择支付宝支付,订单应该是待支付
        status = (OrderInfo.ORDER_STATUS_ENUM['UNPAID']
                  if OrderInfo.PAY_METHODS_ENUM['ALIPAY'] == pay_method
                  else OrderInfo.ORDER_STATUS_ENUM['UNSEND'])

        # 开启一个事务
        with transaction.atomic():

            # 创建事务保存点
            save_point = transaction.savepoint()
            try:

                order = OrderInfo.objects.create(
                    order_id=order_id,
                    user=user,
                    address=address,
                    total_count=0,
                    total_amount=Decimal('0.00'),
                    freight=Decimal('10.00'),
                    pay_method=pay_method,
                    status=status
                )

                # 从redis读取购物车中被勾选的商品信息
                redis_conn = get_redis_connection('cart')
                cart_redis_dict = redis_conn.hgetall('cart_%d' % user.id)
                cart_selected_ids = redis_conn.smembers('selected_%d' % user.id)
                cart_selected_dict = {}
                for sku_id_bytes in cart_selected_ids:
                    cart_selected_dict[int(sku_id_bytes)] = int(cart_redis_dict[sku_id_bytes])

                for sku_id in cart_selected_dict:

                    while True:
                        sku = SKU.objects.get(id=sku_id)
                        sku_count = cart_selected_dict[sku_id]
                        origin_stock = sku.stock
                        origin_sales = sku.sales
                        if sku_count > origin_stock:
                            raise serializers.ValidationError('库存不足')

                        new_stock = origin_stock - sku_count
                        new_sales = origin_sales + sku_count

                        # 减少库存，增加销量 SKU   乐观锁

                        result = SKU.objects.filter(id=sku_id, stock=origin_stock).update(stock=new_stock,sales=new_sales)
                        if result == 0:
                            continue  # 跳出本次循环进入下一次
                        spu = sku.goods
                        spu.sales += sku_count
                        spu.save()
                        OrderGoods.objects.create(
                            order=order,
                            sku=sku,
                            count=sku_count,
                            price=sku.price
                        )

                        order.total_count = order.total_count + sku_count
                        order.total_amount = order.total_amount + (sku.price * sku_count)

                        break
                order.total_amount += order.freight
                order.save()
            except Exception:
                # 暴力回滚,无论中间出现什么问题全部回滚
                transaction.savepoint_rollback(save_point)
                raise
            else:
                transaction.savepoint_commit(save_point)  # 如果中间没有出现异常提交事件

        pl = redis_conn.pipeline()
        pl.hdel('cart_%d' % user.id, *cart_selected_ids)
        pl.srem('selected_%d' % user.id, *cart_selected_ids)
        pl.execute()

        return order
