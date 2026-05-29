from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import inlineformset_factory
from django.db.models import Q

from .models import Estimativa, EstimativaItem, Fase, Orcamento, Registro, format_decimal_hours


MODULO_PROCESSO_CHOICES = [
    ('', '— selecione —'),
    ('AP - Administração de Pessoal', 'AP - Administração de Pessoal'),
    ('CP - Controle do Ponto', 'CP - Controle do Ponto'),
    ('GP - Gestão do Ponto', 'GP - Gestão do Ponto'),
    ('Ponto Mobile', 'Ponto Mobile'),
    ('Ponto Online', 'Ponto Online'),
    ('BS - Benefícios', 'BS - Benefícios'),
    ('S - Segurança', 'S - Segurança'),
    ('M - Medicina', 'M - Medicina'),
    ('QL - Quadro de Vagas', 'QL - Quadro de Vagas'),
    ('OR - Orçamento', 'OR - Orçamento'),
    ('TR - Treinamento', 'TR - Treinamento'),
    ('JR - Jurídico', 'JR - Jurídico'),
    ('RS - Recrutamento e Seleção', 'RS - Recrutamento e Seleção'),
    ('CS - Cargos e Salários', 'CS - Cargos e Salários'),
    ('PG - Painel de Gestão', 'PG - Painel de Gestão'),
    ('Colabbe - Admissão Digital', 'Colabbe - Admissão Digital'),
    ('Bot Factory', 'Bot Factory'),
    ('SING', 'SING'),
    ('BPM', 'BPM'),
    ('Gestão de Carreira e Sucessão', 'Gestão de Carreira e Sucessão'),
    ('Gestão de Remuneração', 'Gestão de Remuneração'),
    ('Gestão de Desempenho', 'Gestão de Desempenho'),
    ('ATS - Recrutamento e Seleção', 'ATS - Recrutamento e Seleção'),
    ('Integrador SST', 'Integrador SST'),
    ('GED', 'GED'),
    ('Outros', 'Outros'),
]


RECURSO_CHOICES = [
    ('', '— selecione —'),
    ('Consultoria de Implantação', 'Consultoria de Implantação'),
    ('Gerente de Projetos', 'Gerente de Projetos'),
    ('Desenvolvedor', 'Desenvolvedor'),
    ('Analista de Infraestrutura', 'Analista de Infraestrutura'),
    ('Consultoria Especializada', 'Consultoria Especializada'),
    ('Análise da Demanda', 'Análise da Demanda'),
]


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('format', '%Y-%m-%d')
        super().__init__(*args, **kwargs)


class TimeInput(forms.TimeInput):
    input_type = 'time'


class DurationField(forms.CharField):
    default_error_messages = {
        'invalid': 'Informe as horas no formato HH:MM.',
    }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            'widget',
            forms.TextInput(
                attrs={
                    'placeholder': '00:00',
                    'pattern': r'^\d{1,4}:[0-5]\d$',
                    'inputmode': 'numeric',
                    'class': 'duration-input',
                }
            ),
        )
        kwargs.setdefault('required', False)
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if value in (None, ''):
            return '00:00'
        try:
            return format_decimal_hours(value)
        except (InvalidOperation, TypeError, ValueError):
            return value

    def to_python(self, value):
        value = super().to_python(value)
        if value in (None, ''):
            return Decimal('0')

        value = value.strip()
        if ':' not in value:
            try:
                return Decimal(value.replace(',', '.'))
            except InvalidOperation as exc:
                raise forms.ValidationError(self.error_messages['invalid'], code='invalid') from exc

        hours_text, minutes_text = value.split(':', 1)
        if not hours_text.isdigit() or not minutes_text.isdigit():
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')

        minutes = int(minutes_text)
        if minutes > 59:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')

        return Decimal(hours_text) + (Decimal(minutes) / Decimal('60'))


class RegistroForm(forms.ModelForm):
    class Meta:
        model = Registro
        fields = ['data', 'orcamento', 'fase', 'hora_inicio', 'hora_fim', 'descricao']
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

        self.fields['fase'].queryset = Fase.objects.order_by('codigo')
        self.fields['fase'].empty_label = '— selecione —'


class OrcamentoForm(forms.ModelForm):
    class Meta:
        model = Orcamento
        fields = ['codigo', 'nome']

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip()


class FaseForm(forms.ModelForm):
    class Meta:
        model = Fase
        fields = ['codigo', 'descricao']

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip()

    def clean_descricao(self):
        return self.cleaned_data['descricao'].strip()


class EstimativaForm(forms.ModelForm):
    class Meta:
        model = Estimativa
        fields = ['cliente', 'solicitante', 'projeto', 'sistema']

    def clean_cliente(self):
        return self.cleaned_data['cliente'].strip()

    def clean_solicitante(self):
        return self.cleaned_data['solicitante'].strip()

    def clean_projeto(self):
        return self.cleaned_data['projeto'].strip()

    def clean_sistema(self):
        return self.cleaned_data['sistema'].strip()


class EstimativaItemForm(forms.ModelForm):
    horas_analise = DurationField()
    horas_atividade = DurationField()
    horas_gp = DurationField()
    horas_estimadas = DurationField(required=False)

    class Meta:
        model = EstimativaItem
        fields = [
            'ordem',
            'modulo_processo',
            'recurso',
            'escopo',
            'horas_analise',
            'horas_atividade',
            'horas_gp',
            'horas_estimadas',
        ]
        widgets = {
            'modulo_processo': forms.Select(choices=MODULO_PROCESSO_CHOICES),
            'recurso': forms.Select(choices=RECURSO_CHOICES),
            'escopo': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['ordem'].required = False
        self.fields['horas_estimadas'].widget.attrs.update(
            {
                'readonly': 'readonly',
                'tabindex': '-1',
                'aria-readonly': 'true',
                'class': 'duration-input calculated-duration',
            }
        )

    def has_changed(self):
        if not self.instance.pk and self.data:
            prefix = self.prefix
            text_values = [
                self.data.get(f'{prefix}-modulo_processo', ''),
                self.data.get(f'{prefix}-recurso', ''),
                self.data.get(f'{prefix}-escopo', ''),
            ]
            duration_values = [
                self.data.get(f'{prefix}-horas_analise', ''),
                self.data.get(f'{prefix}-horas_atividade', ''),
                self.data.get(f'{prefix}-horas_gp', ''),
            ]
            has_text = any(value.strip() for value in text_values)
            has_hours = any(value.strip() not in ('', '00:00', '0', '0:00') for value in duration_values)
            if not has_text and not has_hours:
                return False

        changed_data = set(super().changed_data)
        changed_data.discard('ordem')
        changed_data.discard('horas_estimadas')
        return bool(changed_data)

    def clean_ordem(self):
        return self.cleaned_data.get('ordem')

    def clean_modulo_processo(self):
        return self.cleaned_data['modulo_processo'].strip()

    def clean_recurso(self):
        return self.cleaned_data['recurso'].strip()

    def clean_escopo(self):
        return self.cleaned_data['escopo'].strip()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('DELETE'):
            return cleaned_data

        horas_analise = cleaned_data.get('horas_analise') or Decimal('0')
        horas_atividade = cleaned_data.get('horas_atividade') or Decimal('0')
        cleaned_data['horas_estimadas'] = horas_analise + horas_atividade
        return cleaned_data


EstimativaItemFormSet = inlineformset_factory(
    Estimativa,
    EstimativaItem,
    form=EstimativaItemForm,
    extra=0,
    can_delete=True,
    min_num=0,
    validate_min=False,
)

EstimativaItemCreateFormSet = inlineformset_factory(
    Estimativa,
    EstimativaItem,
    form=EstimativaItemForm,
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
)
