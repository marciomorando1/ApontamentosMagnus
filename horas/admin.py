from django.contrib import admin

from .models import Orcamento, Registro


@admin.register(Orcamento)
class OrcamentoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nome', 'ativo', 'criado_em')
    search_fields = ('codigo', 'nome')
    list_filter = ('ativo',)


@admin.register(Registro)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ('user', 'data', 'hora_inicio', 'hora_fim', 'orcamento', 'total_formatado')
    list_filter = ('user', 'data', 'orcamento')
    search_fields = ('user__username', 'descricao', 'orcamento__codigo', 'orcamento__nome')

# Register your models here.
