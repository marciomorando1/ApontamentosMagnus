from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib import admin

from .models import AgendaAtividade, Fase, Orcamento, Registro, SolicitacaoHoras, UserProfile


User = get_user_model()


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fk_name = 'user'
    fields = ('is_gerente_projetos', 'is_pmo', 'must_change_password')


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
    list_display = (
        'codigo',
        'codigo_cliente',
        'numero_chamado',
        'nome',
        'horas_formatadas',
        'horas_adicionais_formatadas',
        'horas_apontadas_formatadas',
        'horas_disponiveis_formatadas',
        'responsavel',
        'pmo',
        'ativo',
        'criado_em',
    )
    search_fields = ('codigo', 'codigo_cliente', 'numero_chamado', 'nome', 'responsavel__username', 'pmo__username')
    list_filter = ('ativo', 'responsavel', 'pmo')
    readonly_fields = ('horas_adicionais', 'horas_apontadas')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'pmo':
            kwargs['queryset'] = User.objects.filter(profile__is_pmo=True).order_by('username')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change and not obj.responsavel_id:
            obj.responsavel = request.user
        super().save_model(request, obj, form, change)


@admin.register(Registro)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ('user', 'data', 'hora_inicio', 'hora_fim', 'orcamento', 'fase', 'processado', 'total_formatado')
    list_filter = ('user', 'data', 'orcamento', 'fase', 'processado')
    search_fields = ('user__username', 'descricao', 'orcamento__codigo', 'orcamento__nome', 'fase__codigo', 'fase__descricao')

    def delete_queryset(self, request, queryset):
        for registro in queryset:
            registro.delete()


@admin.register(SolicitacaoHoras)
class SolicitacaoHorasAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'solicitante',
        'orcamento',
        'quantidade_horas_formatadas',
        'situacao',
        'decidido_por',
        'criado_em',
    )
    list_filter = ('situacao', 'orcamento', 'solicitante')
    search_fields = ('solicitante__username', 'orcamento__codigo', 'motivo', 'motivo_reprovacao')
    readonly_fields = (
        'solicitante',
        'orcamento',
        'quantidade_horas',
        'motivo',
        'situacao',
        'motivo_reprovacao',
        'decidido_por',
        'criado_em',
        'decidido_em',
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_gerente_projetos', 'is_pmo', 'must_change_password')
    list_filter = ('is_gerente_projetos', 'is_pmo', 'must_change_password')
    search_fields = ('user__username',)


@admin.register(AgendaAtividade)
class AgendaAtividadeAdmin(admin.ModelAdmin):
    list_display = (
        'titulo',
        'user',
        'criado_por',
        'orcamento',
        'data_inicio',
        'hora_inicio',
        'data_fim',
        'hora_fim',
        'total_horas_maximo_formatado',
    )
    list_filter = ('user', 'criado_por', 'data_inicio', 'data_fim', 'orcamento')
    search_fields = ('titulo', 'cliente', 'numero_chamado', 'produto', 'descricao', 'user__username', 'criado_por__username')
