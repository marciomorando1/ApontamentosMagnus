from django.urls import path

from . import views

app_name = 'horas'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('agenda/', views.AgendaView.as_view(), name='agenda'),
    path('agenda/nova/', views.AgendaCreateView.as_view(), name='agenda_nova'),
    path('agenda/<int:pk>/editar/', views.AgendaUpdateView.as_view(), name='agenda_editar'),
    path('agenda/<int:pk>/remover/', views.AgendaDeleteView.as_view(), name='agenda_remover'),
    path('folgas-feriados/', views.FolgasFeriadosView.as_view(), name='folgas_feriados'),
    path('folgas-feriados/<int:pk>/editar/', views.FolgaFeriadoUpdateView.as_view(), name='folga_feriado_editar'),
    path('timer/', views.TimerView.as_view(), name='timer'),
    path('registros/', views.RegistrosView.as_view(), name='registros'),
    path('registros/exportar/', views.exportar_registros_csv, name='exportar_csv'),
    path('registros/<int:pk>/processar/', views.RegistroProcessarView.as_view(), name='registro_processar'),
    path('registros/<int:pk>/editar/', views.RegistroUpdateView.as_view(), name='registro_editar'),
    path('registros/<int:pk>/remover/', views.RegistroDeleteView.as_view(), name='registro_remover'),
    path('estimativas/', views.EstimativasView.as_view(), name='estimativas'),
    path('estimativas/nova/', views.EstimativaCreateView.as_view(), name='estimativa_nova'),
    path('estimativas/<int:pk>/editar/', views.EstimativaUpdateView.as_view(), name='estimativa_editar'),
    path('estimativas/<int:pk>/remover/', views.EstimativaDeleteView.as_view(), name='estimativa_remover'),
    path('estimativas/<int:pk>/exportar/', views.exportar_estimativa_xlsx, name='estimativa_exportar'),
    path('resumo/', views.ResumoView.as_view(), name='resumo'),
    path('solicitacoes-horas/', views.SolicitacoesHorasView.as_view(), name='solicitacoes_horas'),
    path(
        'solicitacoes-horas/pendentes/',
        views.SolicitacoesHorasPendentesView.as_view(),
        name='solicitacoes_horas_pendentes',
    ),
    path(
        'solicitacoes-horas/<int:pk>/decidir/',
        views.SolicitacaoHorasDecisaoView.as_view(),
        name='solicitacao_horas_decidir',
    ),
    path('orcamentos/', views.OrcamentosView.as_view(), name='orcamentos'),
    path('orcamentos/<int:pk>/editar/', views.OrcamentoUpdateView.as_view(), name='orcamento_editar'),
    path('orcamentos/<int:pk>/remover/', views.OrcamentoDeleteView.as_view(), name='orcamento_remover'),
    path('fases/', views.FasesView.as_view(), name='fases'),
    path('fases/<int:pk>/remover/', views.FaseDeleteView.as_view(), name='fase_remover'),
    path('servicos/', views.ServicosView.as_view(), name='servicos'),
    path('servicos/<int:pk>/remover/', views.ServicoDeleteView.as_view(), name='servico_remover'),
]
