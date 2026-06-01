from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib import admin

from .models import AgendaAtividade, Fase, Orcamento, Registro, UserProfile


User = get_user_model()


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fk_name = 'user'


class UserAdmin(DjangoUserAdmin):
    inlines = (UserProfileInline,)


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

admin.site.register(User, UserAdmin)


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
    list_display = ('user', 'data', 'hora_inicio', 'hora_fim', 'orcamento', 'fase', 'processado', 'total_formatado')
    list_filter = ('user', 'data', 'orcamento', 'fase', 'processado')
    search_fields = ('user__username', 'descricao', 'orcamento__codigo', 'orcamento__nome', 'fase__codigo', 'fase__descricao')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_gerente_projetos')
    list_filter = ('is_gerente_projetos',)
    search_fields = ('user__username',)


@admin.register(AgendaAtividade)
class AgendaAtividadeAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'user', 'criado_por', 'orcamento', 'data_inicio', 'data_fim')
    list_filter = ('user', 'criado_por', 'data_inicio', 'data_fim', 'orcamento')
    search_fields = ('titulo', 'cliente', 'numero_chamado', 'produto', 'descricao', 'user__username', 'criado_por__username')
