from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


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


class Orcamento(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nome = models.CharField(max_length=200, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} - {self.nome}' if self.nome else self.codigo


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    is_gerente_projetos = models.BooleanField(default=False)

    def __str__(self):
        role = 'GP' if self.is_gerente_projetos else 'Usuario'
        return f'{self.user} - {role}'


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
    produto = models.CharField(max_length=200, blank=True)
    titulo = models.CharField(max_length=200)
    descricao = models.TextField(blank=True)
    data_inicio = models.DateField()
    data_fim = models.DateField()
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['data_inicio', 'titulo', 'pk']

    def clean(self):
        errors = {}
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            errors['data_fim'] = 'A data final deve ser maior ou igual a data inicial.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.titulo} - {self.user}'


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

    @property
    def total_horas(self):
        inicio = datetime.combine(datetime.min, self.hora_inicio)
        fim = datetime.combine(datetime.min, self.hora_fim)
        delta = fim - inicio
        if delta.days < 0:
            delta += timedelta(days=1)
        return delta.total_seconds() / 3600

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
        if not self.fase_id:
            errors['fase'] = 'Selecione uma fase.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.user} - {self.data} - {self.hora_inicio} às {self.hora_fim}'
