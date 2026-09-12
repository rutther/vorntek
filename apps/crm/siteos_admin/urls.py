from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from console import health as console_health
from console import views as console_views
from leads import webhooks as lead_webhooks

urlpatterns = [
    path('healthz/', console_health.healthz, name='healthz'),
    path('', RedirectView.as_view(url='/admin/', permanent=False)),
    path(
        'admin/login/',
        auth_views.LoginView.as_view(
            template_name='console/login.html',
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path(
        'admin/logout/',
        auth_views.LogoutView.as_view(next_page='/admin/login/'),
        name='logout',
    ),
    path('admin/', include('console.urls')),
    path('api/leads/forms/<str:form_code>/definition/', console_views.public_lead_form_definition, name='public_lead_form_definition'),
    path('api/leads/forms/<str:form_code>/submit/', console_views.public_lead_submit, name='public_lead_submit'),
    path('api/marketing/measurement-config/', console_views.public_marketing_measurement_config, name='public_marketing_measurement_config'),
    path('api/webhooks/meta/leadgen/', lead_webhooks.meta_leadgen_webhook, name='meta_leadgen_webhook'),
    path('api/webhooks/whatsapp/', lead_webhooks.whatsapp_webhook, name='whatsapp_webhook'),
    path('api/webhooks/whatsapp/ycloud/', lead_webhooks.ycloud_whatsapp_webhook, name='ycloud_whatsapp_webhook'),
    path('console/', RedirectView.as_view(url='/admin/', permanent=False)),
]
