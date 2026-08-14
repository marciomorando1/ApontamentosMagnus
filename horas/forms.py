from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm
from django.forms import inlineformset_factory
from django.db.models import Q

from .models import (
    AgendaAtividade,
    Cliente,
    ConfiguracaoSistema,
    Estimativa,
    EstimativaItem,
    Fase,
    FolgaFeriado,
    Orcamento,
    Registro,
    Servico,
    SolicitacaoHoras,
    format_decimal_hours,
)


User = get_user_model()

REGISTRO_DESCRICAO_MAX_LENGTH = 999


class RequiredPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label='Senha atual',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )
    new_password1 = forms.CharField(
        label='Nova senha',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    new_password2 = forms.CharField(
        label='Confirme a nova senha',
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )


class ConfiguracaoSistemaForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoSistema
        fields = ['url_erp', 'usuario_erp', 'senha_erp']
        widgets = {
            'senha_erp': forms.PasswordInput(render_value=True),
        }

    def clean_url_erp(self):
        return self.cleaned_data['url_erp'].strip().rstrip('/')

    def clean_usuario_erp(self):
        return self.cleaned_data['usuario_erp'].strip()

    def clean_senha_erp(self):
        return self.cleaned_data['senha_erp'].strip()


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

AGENDA_PRODUTO_CHOICES = [
    ('', '— selecione —'),
    ('ERP', 'ERP'),
    ('HCM', 'HCM'),
    ('PMO', 'PMO'),
    ('GAS', 'GAS'),
]


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('format', '%Y-%m-%d')
        super().__init__(*args, **kwargs)


class TimeInput(forms.TimeInput):
    input_type = 'time'


class AgendaOrcamentoSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            option['attrs']['data-cliente'] = value.instance.codigo_cliente
            option['attrs']['data-chamado'] = value.instance.numero_chamado
            option['attrs']['data-horas'] = value.instance.horas_formatadas
            option['attrs']['data-horas-apontadas'] = value.instance.horas_apontadas_formatadas
            option['attrs']['data-horas-disponiveis'] = value.instance.horas_disponiveis_formatadas
        return option


class DurationField(forms.CharField):
    default_error_messages = {
        'invalid': 'Informe as horas no formato HH:MM.',
    }
    decimal_places = Decimal('0.01')

    def __init__(self, *args, **kwargs):
        self.compact_digits = kwargs.pop('compact_digits', False)
        widget_attrs = {
            'placeholder': '00:00',
            'pattern': r'^\d+:[0-5]\d$',
            'inputmode': 'numeric',
            'class': 'duration-input',
        }
        if self.compact_digits:
            widget_attrs.update(
                {
                    'data-compact-duration': 'true',
                }
            )
        kwargs.setdefault(
            'widget',
            forms.TextInput(attrs=widget_attrs),
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
            if self.required:
                return None
            return Decimal('0')

        value = value.strip()
        if ':' not in value:
            if self.compact_digits and value.isdigit():
                hours_text = value[:-2] or '0'
                minutes_text = value[-2:]
                minutes = int(minutes_text)
                if minutes > 59:
                    raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
                return self._quantize_hours(
                    Decimal(hours_text) + (Decimal(minutes) / Decimal('60'))
                )
            try:
                return self._quantize_hours(Decimal(value.replace(',', '.')))
            except InvalidOperation as exc:
                raise forms.ValidationError(self.error_messages['invalid'], code='invalid') from exc

        hours_text, minutes_text = value.split(':', 1)
        if not hours_text.isdigit() or not minutes_text.isdigit():
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')

        minutes = int(minutes_text)
        if minutes > 59:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')

        return self._quantize_hours(Decimal(hours_text) + (Decimal(minutes) / Decimal('60')))

    def _quantize_hours(self, value):
        return value.quantize(self.decimal_places)


class RegistroForm(forms.ModelForm):
    class Meta:
        model = Registro
        fields = ['data', 'orcamento', 'fase', 'servico', 'hora_inicio', 'hora_fim', 'descricao']
        widgets = {
            'data': DateInput(),
            'orcamento': AgendaOrcamentoSelect(),
            'hora_inicio': TimeInput(format='%H:%M'),
            'hora_fim': TimeInput(format='%H:%M'),
            'descricao': forms.Textarea(attrs={'placeholder': 'Ex: Dado continuidade no desenvolvimento da integração...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['descricao'].widget.attrs['maxlength'] = REGISTRO_DESCRICAO_MAX_LENGTH
        self.fields['descricao'].widget.attrs['data-no-linebreak'] = 'true'
        if not self.is_bound and not self.instance.pk:
            self.fields['data'].initial = date.today()
        queryset = Orcamento.objects.filter(ativo=True)
        selected_orcamento_id = self.instance.orcamento_id if self.instance.pk else self.initial.get('orcamento')
        if selected_orcamento_id:
            queryset = Orcamento.objects.filter(Q(ativo=True) | Q(pk=selected_orcamento_id))
        self.fields['orcamento'].queryset = queryset.order_by('codigo')
        self.fields['orcamento'].empty_label = '— selecione —'

        self.fields['fase'].queryset = Fase.objects.order_by('codigo')
        self.fields['fase'].empty_label = '— selecione —'
        self.fields['fase'].required = False

        self.fields['servico'].queryset = Servico.objects.order_by('codigo')
        self.fields['servico'].empty_label = '— selecione —'

    def clean_descricao(self):
        descricao = ' '.join(self.cleaned_data['descricao'].splitlines()).strip()
        if len(descricao) > REGISTRO_DESCRICAO_MAX_LENGTH:
            raise forms.ValidationError(
                f'A descricao deve ter no maximo {REGISTRO_DESCRICAO_MAX_LENGTH} caracteres.'
            )
        return descricao


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['Codigo_Cliente', 'Nome_Cliente']
        error_messages = {
            'Codigo_Cliente': {
                'unique': 'Ja existe um cliente com este codigo.',
            },
        }
        widgets = {
            'Codigo_Cliente': forms.TextInput(attrs={'inputmode': 'numeric', 'pattern': r'\d*', 'class': 'numeric-only'}),
        }

    def clean_Codigo_Cliente(self):
        return self.cleaned_data['Codigo_Cliente'].strip()

    def clean_Nome_Cliente(self):
        return self.cleaned_data['Nome_Cliente'].strip()

    def save(self, commit=True, user=None):
        cliente = super().save(commit=False)
        cliente.Situacao = Cliente.SITUACAO_ATIVO
        if user is not None:
            cliente.Usuario_Alteracao = user
        if commit:
            cliente.save()
        return cliente


class ClienteEditForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['Nome_Cliente', 'Situacao']

    def clean_Nome_Cliente(self):
        return self.cleaned_data['Nome_Cliente'].strip()

    def save(self, commit=True, user=None):
        cliente = super().save(commit=False)
        if user is not None:
            cliente.Usuario_Alteracao = user
        if commit:
            cliente.save()
        return cliente


class ClienteImportForm(forms.Form):
    arquivo = forms.FileField(
        label='Planilha Excel',
        widget=forms.FileInput(attrs={'accept': '.xlsx'}),
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Selecione uma planilha no formato XLSX.')
        return arquivo


class OrcamentoForm(forms.ModelForm):
    horas = DurationField(
        label='Quantidade de horas',
        required=True,
        compact_digits=True,
    )
    codigo_cliente = forms.ChoiceField(label='Codigo do Cliente')

    class Meta:
        model = Orcamento
        fields = ['codigo', 'codigo_cliente', 'nome_cliente', 'numero_chamado', 'nome', 'horas', 'pmo']
        error_messages = {
            'codigo': {
                'unique': 'Ja existe um orcamento com este codigo.',
            },
        }
        widgets = {
            'codigo': forms.TextInput(attrs={'inputmode': 'numeric', 'pattern': r'\d*', 'class': 'numeric-only'}),
            'nome_cliente': forms.TextInput(attrs={'readonly': 'readonly', 'class': 'cliente-nome-readonly'}),
            'numero_chamado': forms.TextInput(attrs={'inputmode': 'numeric', 'pattern': r'\d*', 'class': 'numeric-only'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_cliente = None
        clientes = list(Cliente.objects.filter(Situacao=Cliente.SITUACAO_ATIVO).order_by('Codigo_Cliente'))
        current_codigo = self.instance.codigo_cliente if self.instance.pk else ''
        current_cliente = None
        if current_codigo and all(cliente.Codigo_Cliente != current_codigo for cliente in clientes):
            current_cliente = Cliente.objects.filter(Codigo_Cliente=current_codigo).first()
            if current_cliente:
                clientes.append(current_cliente)
        self.clientes_por_codigo = {cliente.Codigo_Cliente: cliente for cliente in clientes}
        self.fields['codigo_cliente'].choices = [
            ('', 'Selecione um cliente'),
            *[
                (cliente.Codigo_Cliente, f'{cliente.Codigo_Cliente} - {cliente.Nome_Cliente}')
                for cliente in clientes
            ],
        ]
        self.fields['codigo_cliente'].widget.attrs.update({'data-cliente-select': 'true'})
        self.fields['nome_cliente'].required = False
        self.fields['nome_cliente'].widget.attrs.update({'readonly': 'readonly', 'data-cliente-nome': 'true'})
        if current_cliente and current_cliente.Situacao != Cliente.SITUACAO_ATIVO:
            self.fields['codigo_cliente'].help_text = 'Cliente atual esta inativo; selecione outro cliente ativo para trocar.'
        self.fields['pmo'].label = 'PMO'
        self.fields['pmo'].queryset = User.objects.filter(profile__is_pmo=True).order_by('username')
        self.fields['pmo'].empty_label = '??? selecione ???'

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip()

    def clean_codigo_cliente(self):
        codigo_cliente = self.cleaned_data['codigo_cliente'].strip()
        cliente = self.clientes_por_codigo.get(codigo_cliente)
        if not cliente:
            raise forms.ValidationError('Selecione um cliente cadastrado.')
        if cliente.Situacao != Cliente.SITUACAO_ATIVO and codigo_cliente != self.instance.codigo_cliente:
            raise forms.ValidationError('Selecione um cliente ativo.')
        self.selected_cliente = cliente
        return codigo_cliente

    def clean_nome_cliente(self):
        return self.cleaned_data.get('nome_cliente', '').strip()

    def clean_numero_chamado(self):
        return self.cleaned_data['numero_chamado'].strip()

    def clean_horas(self):
        horas = self.cleaned_data['horas']
        if horas <= 0:
            raise forms.ValidationError('A quantidade de horas deve ser maior que zero.')
        if self.instance.pk and horas + self.instance.horas_adicionais < self.instance.horas_apontadas:
            raise forms.ValidationError(
                'O total de horas nao pode ser menor que as horas ja apontadas.'
            )
        return horas

    def save(self, commit=True):
        orcamento = super().save(commit=False)
        cliente = self.selected_cliente or self.clientes_por_codigo.get(orcamento.codigo_cliente)
        if cliente:
            orcamento.nome_cliente = cliente.Nome_Cliente
        if commit:
            orcamento.save()
            self.save_m2m()
        return orcamento


class OrcamentoImportForm(forms.Form):
    arquivo = forms.FileField(
        label='Planilha Excel',
        widget=forms.FileInput(attrs={'accept': '.xlsx'}),
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Selecione uma planilha no formato XLSX.')
        return arquivo


class SolicitacaoHorasForm(forms.ModelForm):
    quantidade_horas = DurationField(
        label='Quantidade de Horas',
        required=True,
        compact_digits=True,
    )

    class Meta:
        model = SolicitacaoHoras
        fields = ['orcamento', 'quantidade_horas', 'motivo']
        widgets = {
            'motivo': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['orcamento'].queryset = Orcamento.objects.filter(
            ativo=True,
            responsavel__isnull=False,
        ).order_by('codigo')
        self.fields['orcamento'].empty_label = '— selecione —'

    def clean_quantidade_horas(self):
        quantidade = self.cleaned_data['quantidade_horas']
        if quantidade <= 0:
            raise forms.ValidationError('A quantidade de horas deve ser maior que zero.')
        return quantidade

    def clean_motivo(self):
        return self.cleaned_data['motivo'].strip()


class FaseForm(forms.ModelForm):
    class Meta:
        model = Fase
        fields = ['codigo', 'descricao']

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip()

    def clean_descricao(self):
        return self.cleaned_data['descricao'].strip()


class ServicoForm(forms.ModelForm):
    class Meta:
        model = Servico
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


class FolgaFeriadoForm(forms.ModelForm):
    aplicar_todos = forms.BooleanField(
        label='Criar para todos os usuários',
        required=False,
    )

    class Meta:
        model = FolgaFeriado
        fields = ['user', 'data', 'descricao']
        widgets = {
            'data': DateInput(),
            'descricao': forms.TextInput(attrs={'maxlength': 200}),
        }

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user = current_user
        self.is_gp = bool(
            current_user
            and hasattr(current_user, 'profile')
            and (current_user.profile.is_gerente_projetos or current_user.profile.is_administrador)
        )
        self.fields['user'].queryset = User.objects.order_by('username')
        self.fields['user'].label = 'Usuário'
        self.fields['data'].label = 'Data'
        self.fields['descricao'].label = 'Descrição'

        if self.instance.pk:
            self.fields.pop('aplicar_todos', None)
            if self.instance.abrangencia_todos:
                self.fields['user'].initial = None
                self.fields['user'].widget = forms.HiddenInput()
                self.fields['user'].required = False

        if self.is_gp:
            self.fields['user'].required = False
        else:
            self.fields['user'].queryset = User.objects.filter(pk=getattr(current_user, 'pk', None))
            self.fields['user'].initial = current_user
            self.fields['user'].widget = forms.HiddenInput()
            self.fields['user'].required = False
            self.fields.pop('aplicar_todos', None)

    def clean_descricao(self):
        return self.cleaned_data['descricao'].strip()

    def clean(self):
        cleaned_data = super().clean()
        aplicar_todos = cleaned_data.get('aplicar_todos') or bool(
            self.instance.pk and self.instance.abrangencia_todos
        )
        if not self.is_gp:
            cleaned_data['user'] = self.current_user
        elif aplicar_todos:
            cleaned_data['user'] = None
        elif not cleaned_data.get('user'):
            self.add_error('user', 'Selecione um usuário ou marque para criar para todos.')
        return cleaned_data

class AgendaAtividadeForm(forms.ModelForm):
    produto = forms.ChoiceField(
        choices=AGENDA_PRODUTO_CHOICES,
        required=True,
    )
    quantidade_horas = DurationField(
        label='Quantidade de Horas',
        required=False,
        compact_digits=True,
    )
    total_horas_maximo = DurationField(
        label='Total de Horas Máximo',
        required=True,
        compact_digits=True,
    )

    class Meta:
        model = AgendaAtividade
        fields = [
            'user',
            'cliente',
            'numero_chamado',
            'orcamento',
            'servico',
            'produto',
            'destino_para',
            'titulo',
            'descricao',
            'data_inicio',
            'hora_inicio',
            'data_fim',
            'hora_fim',
            'quantidade_horas',
            'total_horas_maximo',
        ]
        widgets = {
            'orcamento': AgendaOrcamentoSelect(),
            'data_inicio': DateInput(),
            'hora_inicio': TimeInput(format='%H:%M'),
            'data_fim': DateInput(),
            'hora_fim': TimeInput(format='%H:%M'),
            'descricao': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user = current_user
        self.fields['destino_para'].label = 'Atividade para'
        self.fields['destino_para'].required = False
        self.fields['destino_para'].initial = AgendaAtividade.DESTINO_INTERNO
        self.fields['hora_inicio'].required = False
        self.fields['hora_fim'].required = False
        if not self.instance.pk:
            self.fields['cliente'].required = False
            self.fields['cliente'].widget.attrs['readonly'] = 'readonly'
            self.fields['numero_chamado'].widget.attrs['readonly'] = 'readonly'
        if self.instance.pk and self.instance.produto:
            produtos_disponiveis = {value for value, _ in self.fields['produto'].choices}
            if self.instance.produto not in produtos_disponiveis:
                self.fields['produto'].choices = [
                    *self.fields['produto'].choices,
                    (self.instance.produto, self.instance.produto),
                ]
        queryset = Orcamento.objects.filter(ativo=True)
        if self.instance.pk and self.instance.orcamento_id:
            queryset = Orcamento.objects.filter(Q(ativo=True) | Q(pk=self.instance.orcamento_id))
        self.fields['orcamento'].queryset = queryset.order_by('codigo')
        self.fields['orcamento'].empty_label = '— selecione —'
        self.fields['orcamento'].widget.attrs.update(
            {
                'data-searchable-select': 'true',
                'data-search-placeholder': 'Digite para filtrar orcamentos',
                'data-search-empty': 'Nenhum orcamento encontrado',
            }
        )

        self.fields['servico'].queryset = Servico.objects.order_by('codigo')
        self.fields['servico'].empty_label = '— selecione —'

        is_gp = bool(
            current_user
            and hasattr(current_user, 'profile')
            and (current_user.profile.is_gerente_projetos or current_user.profile.is_administrador)
        )
        self.is_gp = is_gp
        self.fields['user'].queryset = User.objects.order_by('username')
        self.fields['user'].label = 'Usuário'

        if is_gp:
            self.fields['user'].required = True
        else:
            self.fields['user'].queryset = User.objects.filter(pk=getattr(current_user, 'pk', None))
            self.fields['user'].initial = current_user
            self.fields['user'].widget = forms.HiddenInput()
            self.fields['user'].required = False

    def clean_cliente(self):
        return self.cleaned_data['cliente'].strip()

    def clean_numero_chamado(self):
        return self.cleaned_data['numero_chamado'].strip()

    def clean_produto(self):
        return self.cleaned_data['produto'].strip()

    def clean_titulo(self):
        return self.cleaned_data['titulo'].strip()

    def clean_descricao(self):
        return self.cleaned_data['descricao'].strip()

    def clean_total_horas_maximo(self):
        total_horas_maximo = self.cleaned_data['total_horas_maximo']
        if total_horas_maximo <= 0:
            raise forms.ValidationError('O total de horas máximo deve ser maior que zero.')
        return total_horas_maximo

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk and cleaned_data.get('orcamento'):
            orcamento = cleaned_data['orcamento']
            cleaned_data['cliente'] = orcamento.codigo_cliente
            cleaned_data['numero_chamado'] = orcamento.numero_chamado
            if not orcamento.codigo_cliente:
                self.add_error('cliente', 'O orçamento selecionado não possui código do cliente.')


        destino_para = cleaned_data.get('destino_para') or AgendaAtividade.DESTINO_INTERNO
        quantidade_horas = cleaned_data.get('quantidade_horas')
        hora_inicio = cleaned_data.get('hora_inicio')
        hora_fim = cleaned_data.get('hora_fim')

        if destino_para == AgendaAtividade.DESTINO_INTERNO:
            cleaned_data['quantidade_horas'] = None
            if not hora_inicio:
                self.add_error('hora_inicio', 'Este campo é obrigatório.')
            if not hora_fim:
                self.add_error('hora_fim', 'Este campo é obrigatório.')
        elif destino_para == AgendaAtividade.DESTINO_TERCEIRO:
            cleaned_data['hora_inicio'] = None
            cleaned_data['hora_fim'] = None
            if not quantidade_horas or quantidade_horas <= 0:
                self.add_error('quantidade_horas', 'A quantidade de horas deve ser maior que zero.')

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
