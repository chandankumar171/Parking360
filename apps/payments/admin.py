from django.contrib import admin

from .models import OwnerLedger, Payment, Refund, WebhookEvent


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "kind", "amount", "status", "razorpay_order_id", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("razorpay_order_id", "razorpay_payment_id")
    readonly_fields = ("raw_payload",)


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "amount", "status", "retry_count", "created_at")
    list_filter = ("status",)


@admin.register(OwnerLedger)
class OwnerLedgerAdmin(admin.ModelAdmin):
    list_display = ("owner", "entry_type", "amount", "balance_after", "created_at")
    list_filter = ("entry_type",)

    def has_change_permission(self, request, obj=None):
        return False  # the ledger is append-only

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(WebhookEvent)
