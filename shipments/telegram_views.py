# Add this to your shipments/views.py or create a separate telegram_views.py

import json
import logging
from django.http import JsonResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request):
    """
    Django view to handle Telegram webhook requests
    This function receives updates from Telegram and processes them
    """
    try:
        # Parse the JSON data from Telegram
        try:
            webhook_data = json.loads(request.body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Invalid JSON in webhook request: {e}")
            return JsonResponse({
                'status': 'error', 
                'message': 'Invalid JSON data'
            }, status=400)
        
        # Validate that we have required fields
        if 'message' not in webhook_data:
            logger.warning("Webhook received without message field")
            return JsonResponse({
                'status': 'ignored', 
                'reason': 'No message in webhook'
            })
        
        # Initialize and use the bot
        try:
            from .telegram_bot import TelegramBot
            bot = TelegramBot()
            result = bot.webhook_handler(webhook_data)
            
            return JsonResponse(result)
            
        except Exception as e:
            logger.error(f"Error in telegram bot processing: {e}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': 'Bot processing failed'
            }, status=500)
            
    except Exception as e:
        logger.error(f"Unexpected error in telegram webhook view: {e}", exc_info=True)
        return JsonResponse({
            'status': 'error',
            'message': 'Internal server error'
        }, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class TelegramWebhookView(View):
    """
    Class-based view alternative for Telegram webhook
    """
    
    def post(self, request):
        """Handle POST requests from Telegram"""
        return telegram_webhook(request)
    
    def get(self, request):
        """Handle GET requests (for webhook verification if needed)"""
        return JsonResponse({
            'status': 'ok',
            'message': 'Telegram webhook endpoint is active'
        })
    
    def http_method_not_allowed(self, request):
        """Handle unsupported HTTP methods"""
        return JsonResponse({
            'status': 'error',
            'message': 'Only POST and GET methods are allowed'
        }, status=405)


# Webhook URL setup for your urls.py:
"""
Add this to your shipments/urls.py:

    # Telegram Bot Webhook
    path('telegram/webhook/', views.telegram_webhook, name='telegram-webhook'),
    # Or use the class-based view:
    # path('telegram/webhook/', views.TelegramWebhookView.as_view(), name='telegram-webhook'),
"""

# Health check view for monitoring
@require_http_methods(["GET"])
def telegram_health_check(request):
    """
    Health check endpoint for monitoring the Telegram bot service
    """
    try:
        from .telegram_bot import TelegramBot
        
        # Try to initialize the bot
        bot = TelegramBot()
        
        # Check if bot token is configured
        if not bot.bot_token:
            return JsonResponse({
                'status': 'error',
                'message': 'Bot token not configured'
            }, status=500)
        
        # Check database connectivity
        from .models import TR830ProcessingState
        state_count = TR830ProcessingState.objects.count()
        
        return JsonResponse({
            'status': 'healthy',
            'message': 'Telegram bot service is operational',
            'active_tr830_states': state_count,
            'timestamp': timezone.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JsonResponse({
            'status': 'unhealthy',
            'message': str(e)
        }, status=500)