from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


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


class Registro(models.Model):
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
