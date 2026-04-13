from django.contrib import admin

from .models import Fase, Orcamento, Registro


@admin.register(Fase)
class FaseAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'descricao', 'criado_em')
    search_fields = ('codigo', 'descricao')


@admin.register(Orcamento)
class OrcamentoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nome', 'ativo', 'criado_em')
    search_fields = ('codigo', 'nome')
    list_filter = ('ativo',)


@admin.register(Registro)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ('user', 'data', 'hora_inicio', 'hora_fim', 'orcamento', 'fase', 'total_formatado')
    list_filter = ('user', 'data', 'orcamento', 'fase')
    search_fields = ('user__username', 'descricao', 'orcamento__codigo', 'orcamento__nome', 'fase__codigo', 'fase__descricao')

# Register your models here.
