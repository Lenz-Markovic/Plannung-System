from django.conf import settings


def site(request):
    """Values every template may need."""
    return {"DEMO_BANNER": settings.DEMO_BANNER}
