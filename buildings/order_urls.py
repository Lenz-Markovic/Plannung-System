from django.urls import path

from . import material_views, order_views as views

app_name = "orders"

urlpatterns = [
    path("", views.order_list, name="list"),
    path("<int:pk>/zeile/", views.order_row, name="row"),
    path("<int:pk>/speichern/", views.order_update, name="update"),
    path("auswahl/", views.order_select, name="select"),
    path("auswahl/leeren/", views.order_select_clear, name="select_clear"),
    path("material/", material_views.material_page, name="material"),
    path("material/preis/", material_views.material_price, name="material_price"),
]
