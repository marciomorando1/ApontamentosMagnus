from django.urls import path

from . import views

app_name = 'horas'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('timer/', views.TimerView.as_view(), name='timer'),
    path('registros/', views.RegistrosView.as_view(), name='registros'),
    path('registros/exportar/', views.exportar_registros_csv, name='exportar_csv'),
    path('registros/<int:pk>/editar/', views.RegistroUpdateView.as_view(), name='registro_editar'),
    path('registros/<int:pk>/remover/', views.RegistroDeleteView.as_view(), name='registro_remover'),
    path('resumo/', views.ResumoView.as_view(), name='resumo'),
    path('orcamentos/', views.OrcamentosView.as_view(), name='orcamentos'),
    path('orcamentos/<int:pk>/remover/', views.OrcamentoDeleteView.as_view(), name='orcamento_remover'),
    path('fases/', views.FasesView.as_view(), name='fases'),
    path('fases/<int:pk>/remover/', views.FaseDeleteView.as_view(), name='fase_remover'),
]
