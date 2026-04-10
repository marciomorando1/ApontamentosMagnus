from datetime import date

from django import forms
from django.db.models import Q

from .models import Orcamento, Registro


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('format', '%Y-%m-%d')
        super().__init__(*args, **kwargs)


class TimeInput(forms.TimeInput):
    input_type = 'time'


class RegistroForm(forms.ModelForm):
    class Meta:
        model = Registro
        fields = ['data', 'orcamento', 'hora_inicio', 'hora_fim', 'descricao']
        widgets = {
            'data': DateInput(),
            'hora_inicio': TimeInput(format='%H:%M'),
            'hora_fim': TimeInput(format='%H:%M'),
            'descricao': forms.Textarea(attrs={'placeholder': 'Ex: Dado continuidade no desenvolvimento da integração...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields['data'].initial = date.today()
        queryset = Orcamento.objects.filter(ativo=True)
        if self.instance.pk and self.instance.orcamento_id:
            queryset = Orcamento.objects.filter(Q(ativo=True) | Q(pk=self.instance.orcamento_id))
        self.fields['orcamento'].queryset = queryset.order_by('codigo')
        self.fields['orcamento'].empty_label = '— selecione —'


class OrcamentoForm(forms.ModelForm):
    class Meta:
        model = Orcamento
        fields = ['codigo', 'nome']

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip()
