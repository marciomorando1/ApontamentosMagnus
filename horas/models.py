from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction


somente_numeros_validator = RegexValidator(
    regex=r'^\d+$',
    message='Informe somente números.',
)


def format_decimal_hours(value):
    value = value or Decimal('0')
    total_minutes = int(round(Decimal(value) * 60))
    hours, minutes = divmod(total_minutes, 60)
    return f'{hours:02d}:{minutes:02d}'


class Fase(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    descricao = models.CharField(max_length=200)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} - {self.descricao}'


class Servico(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    descricao = models.CharField(max_length=200)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} - {self.descricao}'


class Orcamento(models.Model):
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orcamentos',
        null=True,
        blank=True,
    )
    pmo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orcamentos_pmo',
        null=True,
        blank=True,
    )
    codigo = models.CharField(max_length=20, unique=True, validators=[somente_numeros_validator])
    codigo_cliente = models.CharField(max_length=50, blank=True, validators=[somente_numeros_validator])
    nome_cliente = models.CharField(max_length=200, blank=True)
    numero_chamado = models.CharField(max_length=100, blank=True, validators=[somente_numeros_validator])
    nome = models.CharField(max_length=200, blank=True)
    horas = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_adicionais = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_apontadas = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} - {self.nome}' if self.nome else self.codigo

    @property
    def horas_formatadas(self):
        return format_decimal_hours(self.horas)

    @property
    def horas_apontadas_formatadas(self):
        return format_decimal_hours(self.horas_apontadas)

    @property
    def horas_adicionais_formatadas(self):
        return format_decimal_hours(self.horas_adicionais)

    @property
    def total_horas_disponibilizadas(self):
        return self.horas + self.horas_adicionais

    @property
    def total_horas_disponibilizadas_formatadas(self):
        return format_decimal_hours(self.total_horas_disponibilizadas)

    @property
    def horas_disponiveis(self):
        return max(Decimal('0'), self.total_horas_disponibilizadas - self.horas_apontadas)

    @property
    def horas_disponiveis_formatadas(self):
        return format_decimal_hours(self.horas_disponiveis)

    @property
    def responsavel_nome(self):
        if not self.responsavel:
            return ''
        return self.responsavel.get_full_name() or self.responsavel.username

    @property
    def pmo_nome(self):
        if not self.pmo:
            return ''
        return self.pmo.get_full_name() or self.pmo.username

    def clean(self):
        if self.pmo_id and not getattr(getattr(self.pmo, 'profile', None), 'is_pmo', False):
            raise ValidationError({'pmo': 'Selecione um usuario marcado como PMO.'})
        if self.total_horas_disponibilizadas < self.horas_apontadas:
            raise ValidationError(
                {'horas': 'A quantidade de horas não pode ser menor que as horas já apontadas.'}
            )


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    is_gerente_projetos = models.BooleanField(default=False)
    is_administrador = models.BooleanField('Administrador', default=False)
    is_pmo = models.BooleanField(default=False)
    exportacsv = models.BooleanField('Exporta CSV', default=False)
    codigoerp = models.PositiveIntegerField('Código ERP', default=0)
    must_change_password = models.BooleanField(
        'Exigir troca de senha no proximo login',
        default=False,
    )

    def __str__(self):
        roles = []
        if self.is_gerente_projetos:
            roles.append('GP')
        if self.is_administrador:
            roles.append('Administrador')
        if self.is_pmo:
            roles.append('PMO')
        role = ', '.join(roles) if roles else 'Usuario'
        return f'{self.user} - {role}'


class SolicitacaoHoras(models.Model):
    SITUACAO_AGUARDANDO = 'AGUARDANDO'
    SITUACAO_APROVADO = 'APROVADO'
    SITUACAO_REPROVADO = 'REPROVADO'
    SITUACAO_CHOICES = (
        (SITUACAO_AGUARDANDO, 'Aguardando Aprovação'),
        (SITUACAO_APROVADO, 'Aprovado'),
        (SITUACAO_REPROVADO, 'Reprovado'),
    )

    solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='solicitacoes_horas',
    )
    orcamento = models.ForeignKey(
        Orcamento,
        on_delete=models.PROTECT,
        related_name='solicitacoes_horas',
    )
    quantidade_horas = models.DecimalField(max_digits=8, decimal_places=2)
    motivo = models.TextField()
    situacao = models.CharField(
        max_length=10,
        choices=SITUACAO_CHOICES,
        default=SITUACAO_AGUARDANDO,
    )
    motivo_reprovacao = models.TextField(blank=True)
    decidido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='solicitacoes_horas_decididas',
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    decidido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criado_em', '-pk']

    @property
    def numero_solicitacao(self):
        return self.pk

    @property
    def quantidade_horas_formatadas(self):
        return format_decimal_hours(self.quantidade_horas)

    def __str__(self):
        return f'Solicitação {self.pk} - {self.orcamento}'


class Estimativa(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='estimativas',
    )
    cliente = models.CharField(max_length=200)
    solicitante = models.CharField(max_length=200)
    projeto = models.CharField(max_length=250)
    sistema = models.CharField(max_length=200)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-atualizado_em', '-criado_em']

    @property
    def total_horas_estimadas(self):
        return sum(item.horas_estimadas_calculadas for item in self.itens.all())

    @property
    def total_horas_estimadas_formatado(self):
        return format_decimal_hours(self.total_horas_estimadas)

    def __str__(self):
        return f'{self.cliente} - {self.projeto}'


class EstimativaItem(models.Model):
    estimativa = models.ForeignKey(
        Estimativa,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    ordem = models.PositiveIntegerField(default=1)
    modulo_processo = models.CharField(max_length=200, blank=True)
    recurso = models.CharField(max_length=200, blank=True)
    escopo = models.TextField()
    horas_analise = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_atividade = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_gp = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    horas_estimadas = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    class Meta:
        ordering = ['ordem', 'pk']

    @property
    def horas_analise_formatado(self):
        return format_decimal_hours(self.horas_analise)

    @property
    def horas_atividade_formatado(self):
        return format_decimal_hours(self.horas_atividade)

    @property
    def horas_gp_formatado(self):
        return format_decimal_hours(self.horas_gp)

    @property
    def horas_estimadas_formatado(self):
        return format_decimal_hours(self.horas_estimadas_calculadas)

    @property
    def horas_estimadas_calculadas(self):
        horas_analise = Decimal(str(self.horas_analise or '0'))
        horas_atividade = Decimal(str(self.horas_atividade or '0'))
        return horas_analise + horas_atividade

    def save(self, *args, **kwargs):
        self.horas_estimadas = self.horas_estimadas_calculadas
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.ordem} - {self.estimativa}'


class AgendaAtividade(models.Model):
    DESTINO_INTERNO = 'INTERNO'
    DESTINO_TERCEIRO = 'TERCEIRO'
    DESTINO_CHOICES = (
        (DESTINO_INTERNO, 'Interno'),
        (DESTINO_TERCEIRO, 'Terceiro'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='agenda_atividades',
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='agenda_atividades_criadas',
    )
    cliente = models.CharField(max_length=200)
    numero_chamado = models.CharField(max_length=100, blank=True)
    orcamento = models.ForeignKey(
        Orcamento,
        on_delete=models.PROTECT,
        related_name='agenda_atividades',
    )
    servico = models.ForeignKey(
        Servico,
        on_delete=models.PROTECT,
        related_name='agenda_atividades',
        null=True,
    )
    produto = models.CharField(max_length=200, blank=True)
    destino_para = models.CharField(max_length=20, choices=DESTINO_CHOICES, default=DESTINO_INTERNO)
    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fim = models.TimeField(null=True, blank=True)
    quantidade_horas = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    total_horas_maximo = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['data_inicio', 'hora_inicio', 'titulo', 'pk']

    @property
    def quantidade_horas_formatada(self):
        if self.quantidade_horas is None:
            return ''
        return format_decimal_hours(self.quantidade_horas)

    @property
    def total_horas_maximo_formatado(self):
        if self.total_horas_maximo is None:
            return ''
        return format_decimal_hours(self.total_horas_maximo)

    def clean(self):
        errors = {}
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            errors['data_fim'] = 'A data final deve ser maior ou igual a data inicial.'
        elif (
            self.data_inicio
            and self.data_fim
            and self.data_inicio == self.data_fim
            and self.hora_inicio
            and self.hora_fim
            and self.hora_fim <= self.hora_inicio
        ):
            errors['hora_fim'] = 'A hora final deve ser maior que a hora inicial.'
        if self.total_horas_maximo is not None and self.total_horas_maximo <= 0:
            errors['total_horas_maximo'] = 'O total de horas máximo deve ser maior que zero.'
        if not self.servico_id:
            errors['servico'] = 'Selecione um serviço.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.titulo} - {self.user}'


class FolgaFeriado(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='folgas_feriados',
        null=True,
        blank=True,
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='folgas_feriados_criadas',
    )
    data = models.DateField()
    descricao = models.CharField(max_length=200)
    abrangencia_todos = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data', '-abrangencia_todos', 'user__username', 'pk']

    @property
    def user_nome(self):
        if self.abrangencia_todos:
            return 'Todos'
        return self.user.get_full_name() or self.user.username

    @property
    def criado_por_nome(self):
        return self.criado_por.get_full_name() or self.criado_por.username

    def __str__(self):
        return f'{self.data} - {self.user_nome}'

class Registro(models.Model):
    PROCESSADO_SIM = 'S'
    PROCESSADO_NAO = 'N'
    PROCESSADO_CHOICES = (
        (PROCESSADO_SIM, 'Sim'),
        (PROCESSADO_NAO, 'Não'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='registros_horas',
    )
    orcamento = models.ForeignKey(
        Orcamento,
        on_delete=models.PROTECT,
        related_name='registros',
    )
    fase = models.ForeignKey(
        Fase,
        on_delete=models.PROTECT,
        related_name='registros',
        null=True,
        blank=True,
    )
    servico = models.ForeignKey(
        Servico,
        on_delete=models.PROTECT,
        related_name='registros',
        null=True,
    )
    data = models.DateField()
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()
    processado = models.CharField(
        max_length=1,
        choices=PROCESSADO_CHOICES,
        default=PROCESSADO_NAO,
    )
    descricao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data', '-hora_inicio', '-criado_em']

    @staticmethod
    def _normalizar_hora(value):
        if isinstance(value, str):
            return datetime.strptime(value, '%H:%M').time()
        return value

    @property
    def total_horas(self):
        inicio = datetime.combine(datetime.min, self._normalizar_hora(self.hora_inicio))
        fim = datetime.combine(datetime.min, self._normalizar_hora(self.hora_fim))
        delta = fim - inicio
        if delta.days < 0:
            delta += timedelta(days=1)
        return delta.total_seconds() / 3600

    @property
    def total_horas_decimal(self):
        inicio = datetime.combine(datetime.min, self._normalizar_hora(self.hora_inicio))
        fim = datetime.combine(datetime.min, self._normalizar_hora(self.hora_fim))
        delta = fim - inicio
        if delta.days < 0:
            delta += timedelta(days=1)
        return (Decimal(delta.total_seconds()) / Decimal('3600')).quantize(Decimal('0.01'))

    @property
    def total_formatado(self):
        minutos = int(round(self.total_horas * 60))
        horas, minutos = divmod(minutos, 60)
        return f'{horas}h{minutos:02d}'

    def clean(self):
        errors = {}
        if self.data and self.data > date.today():
            errors['data'] = 'A data não pode estar no futuro.'
        if self.hora_inicio and self.hora_fim and self.hora_inicio >= self.hora_fim:
            errors['hora_fim'] = 'A hora final deve ser maior que a hora inicial.'
        if self.orcamento_id and not self.orcamento.ativo:
            errors['orcamento'] = 'Selecione um orçamento ativo.'
        if not self.servico_id:
            errors['servico'] = 'Selecione um serviço.'
        if errors:
            raise ValidationError(errors)

    @staticmethod
    def _limite_orcamento_message(orcamento):
        responsavel = orcamento.responsavel_nome or 'gerente de projetos responsável'
        return (
            'A quantidade de horas apontada excede as horas disponíveis deste orçamento. '
            f'Entre em contato com {responsavel}.'
        )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            anterior = None
            if self.pk:
                anterior = Registro.objects.select_for_update().filter(pk=self.pk).first()

            orcamento_ids = {self.orcamento_id}
            if anterior:
                orcamento_ids.add(anterior.orcamento_id)
            orcamentos = {
                item.pk: item
                for item in Orcamento.objects.select_for_update().filter(pk__in=sorted(orcamento_ids))
            }

            if anterior:
                orcamento_anterior = orcamentos[anterior.orcamento_id]
                orcamento_anterior.horas_apontadas = max(
                    Decimal('0'),
                    orcamento_anterior.horas_apontadas - anterior.total_horas_decimal,
                )

            orcamento_atual = orcamentos[self.orcamento_id]
            novo_total = orcamento_atual.horas_apontadas + self.total_horas_decimal
            if novo_total > orcamento_atual.total_horas_disponibilizadas:
                raise ValidationError({'orcamento': self._limite_orcamento_message(orcamento_atual)})

            if anterior and anterior.orcamento_id != self.orcamento_id:
                orcamento_anterior.save(update_fields=['horas_apontadas'])
            orcamento_atual.horas_apontadas = novo_total
            orcamento_atual.save(update_fields=['horas_apontadas'])
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            orcamento = Orcamento.objects.select_for_update().get(pk=self.orcamento_id)
            orcamento.horas_apontadas = max(
                Decimal('0'),
                orcamento.horas_apontadas - self.total_horas_decimal,
            )
            orcamento.save(update_fields=['horas_apontadas'])
            return super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.user} - {self.data} - {self.hora_inicio} às {self.hora_fim}'
