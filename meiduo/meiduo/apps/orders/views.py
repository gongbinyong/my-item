from decimal import Decimal
import time

from django.shortcuts import render

# Create your views here.
from django_redis import get_redis_connection
from rest_framework import status
from rest_framework.generics import CreateAPIView, ListAPIView, UpdateAPIView, GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from goods.models import SKU
from orders.models import OrderInfo, OrderGoods
from .serializers import OrderSettlementSerializer, CommitOrderSerializer, GetOrdersSerializer, OrderGoodSerializer, \
    CommentsSerializer, UpdateOrderGoodSerializer


class OrderSettlementView(APIView):
    """去结算接口"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        redis_conn = get_redis_connection('cart')
        redis_cart = redis_conn.hgetall("cart_%d" % user.id)
        cart_selected = redis_conn.smembers("selected_%d" % user.id)
        cart = {}
        for sku_id in cart_selected:
            cart[int(sku_id)] = int(redis_cart[sku_id])
        skus = SKU.objects.filter(id__in=cart.keys())
        for sku in skus:
            sku.count = cart[sku.id]
        freight = Decimal('10.00')
        serializer = OrderSettlementSerializer({"freight": freight, 'skus': skus})

        return Response(serializer.data)


class CommitOrderView(CreateAPIView):
    # 指定权限
    permission_classes = [IsAuthenticated]

    # 指定序列化器
    serializer_class = CommitOrderSerializer


    # def get(self, request):
    #     orders = self.get_queryset()
    #     serializer = GetOrdersSerializer(orders, many=True)
    #     return Response(serializer.data)


class GetOrdersView(ListAPIView):
    """获取订单列表"""
    permission_classes = [IsAuthenticated]
    serializer_class = GetOrdersSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = OrderInfo.objects.filter(user=user).order_by('order_id')
        for order in queryset:
            order.create_time = order.create_time.strftime('%Y-%m-%d %H:%M:%S')
        return queryset


# '/orders/'+this.order_id+'/uncommentgoods/'
class UncommentgoodsView(APIView):
    """待评价订单展示界面"""
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        try:
            OrderInfo.objects.get(order_id=order_id, status=4)
        except OrderInfo.DoesNotExist:
            return Response({'message': '订单信息有误'}, status=status.HTTP_400_BAD_REQUEST)
        ordergoods = OrderGoods.objects.filter(order_id=order_id, is_commented=False).order_by('order_id')
        serializer = OrderGoodSerializer(instance=ordergoods, many=True)
        return Response(serializer.data)


# POST /orders/20190220092418000000004/comments/
class SavecommentView(APIView):
    """保存商品评价"""
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        data = request.data
        sku_id = data.get("sku")
        try:
            instance = OrderGoods.objects.get(order_id=order_id, sku_id=sku_id)
        except OrderGoods.DoesNotExist:
            return Response({"message": "该订单商品不存在"}, status=status.HTTP_404_NOT_FOUND, )
        else:
            serializer = UpdateOrderGoodSerializer(instance, data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)


# /skus/'+this.sku_id+'/comments/',
class SKUcommentsView(APIView):
    """商品评价展示界面"""
    def get(self, request, sku_id):
        comments = OrderGoods.objects.filter(sku_id=sku_id, is_commented=True).order_by("order_id")
        for comment in comments:
            comment.username = comment.order.user.username
            if comment.is_anonymous:
                comment.username = '******'
        serializer = CommentsSerializer(comments, many=True)
        return Response(serializer.data)
