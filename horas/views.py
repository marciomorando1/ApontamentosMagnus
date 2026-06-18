import csv
import calendar
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .forms import (
    AgendaAtividadeForm,
    EstimativaForm,
    EstimativaItemCreateFormSet,
    EstimativaItemFormSet,
    FaseForm,
    OrcamentoForm,
    OrcamentoImportForm,
    RegistroForm,
    RequiredPasswordChangeForm,
    SolicitacaoHorasForm,
)
from .models import (
    AgendaAtividade,
    Estimativa,
    Fase,
    Orcamento,
    Registro,
    SolicitacaoHoras,
    UserProfile,
)


XLSX_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('', XLSX_NS)
ET.register_namespace('r', REL_NS)
User = get_user_model()
MONTH_LABELS = [
    '',
    'Janeiro',
    'Fevereiro',
    'Março',
    'Abril',
    'Maio',
    'Junho',
    'Julho',
    'Agosto',
    'Setembro',
    'Outubro',
    'Novembro',
    'Dezembro',
]


def _build_timer_rows_from_post(request):
    rows = [
        {
            'hora_inicio': request.POST.get('hora_inicio', '').strip(),
            'hora_fim': request.POST.get('hora_fim', '').strip(),
            'descricao': request.POST.get('descricao', '').strip(),
        }
    ]

    extra_inicios = request.POST.getlist('extra_hora_inicio')
    extra_fins = request.POST.getlist('extra_hora_fim')
    extra_descricoes = request.POST.getlist('extra_descricao')

    for inicio, fim, descricao in zip(extra_inicios, extra_fins, extra_descricoes):
        rows.append(
            {
                'hora_inicio': inicio.strip(),
                'hora_fim': fim.strip(),
                'descricao': descricao.strip(),
            }
        )
    return rows


def _add_model_validation_to_form(form, error):
    if hasattr(error, 'message_dict'):
        for field, messages_list in error.message_dict.items():
            target_field = field if field in form.fields else None
            for message in messages_list:
                form.add_error(target_field, message)
        return
    for message in error.messages:
        form.add_error(None, message)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_month(value):
    if not value:
        return date.today().replace(day=1)
    try:
        return datetime.strptime(value, '%Y-%m').date().replace(day=1)
    except ValueError:
        return date.today().replace(day=1)


def _base_registros_queryset(user):
    return Registro.objects.select_related('orcamento', 'fase').filter(user=user)


def _user_is_gp(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile.is_gerente_projetos


def _base_agenda_queryset():
    return AgendaAtividade.objects.select_related('user', 'criado_por', 'orcamento')


def _agenda_manage_queryset(user):
    if _user_is_gp(user):
        return _base_agenda_queryset().filter(Q(user=user) | Q(criado_por=user))
    return _base_agenda_queryset().filter(user=user, criado_por=user)


def _can_manage_agenda_activity(user, atividade):
    if _user_is_gp(user):
        return atividade.user_id == user.pk or atividade.criado_por_id == user.pk
    return atividade.user_id == user.pk and atividade.criado_por_id == user.pk


def _month_bounds(month_start):
    first_day = month_start.replace(day=1)
    last_day = date(
        first_day.year,
        first_day.month,
        calendar.monthrange(first_day.year, first_day.month)[1],
    )
    return first_day, last_day


def _month_navigation(month_start):
    prev_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return prev_month, next_month


def _build_agenda_calendar(month_start, atividades, selected_users=None):
    first_day, last_day = _month_bounds(month_start)
    cal = calendar.Calendar(firstweekday=6)
    atividades_por_dia = defaultdict(list)
    selected_users = list(selected_users or [])

    for atividade in atividades:
        current_day = max(atividade.data_inicio, first_day)
        final_day = min(atividade.data_fim, last_day)
        while current_day <= final_day:
            atividades_por_dia[current_day].append(atividade)
            current_day += timedelta(days=1)

    for items in atividades_por_dia.values():
        items.sort(
            key=lambda atividade: (
                atividade.hora_inicio is None,
                atividade.hora_inicio,
                atividade.titulo,
                atividade.pk,
            )
        )

    weeks = []
    for week in cal.monthdatescalendar(first_day.year, first_day.month):
        week_days = []
        for day in week:
            items = list(atividades_por_dia.get(day, []))
            user_groups = []
            if selected_users:
                for selected_user in selected_users:
                    user_items = [
                        atividade for atividade in items if atividade.user_id == selected_user.pk
                    ]
                    user_groups.append(
                        {
                            'user': selected_user,
                            'atividades': user_items,
                        }
                    )
            week_days.append(
                {
                    'date': day,
                    'in_month': day.month == first_day.month,
                    'is_today': day == date.today(),
                    'atividades': items,
                    'user_groups': user_groups,
                }
            )
        weeks.append(week_days)
    return weeks


def _agenda_filter_context(month_start, selected_users):
    prev_month, next_month = _month_navigation(month_start)
    return {
        'mes': month_start.strftime('%Y-%m'),
        'mes_label': f'{MONTH_LABELS[month_start.month]} de {month_start.year}',
        'mes_anterior': prev_month.strftime('%Y-%m'),
        'mes_proximo': next_month.strftime('%Y-%m'),
        'selected_users': selected_users,
    }


def _filter_registros(request):
    queryset = _base_registros_queryset(request.user)
    data_inicial = _parse_date(request.GET.get('de'))
    data_final = _parse_date(request.GET.get('ate'))
    orcamento_id = request.GET.get('orcamento')

    if not data_inicial and not data_final:
        data_inicial = date.today()
        data_final = date.today()

    if data_inicial and data_final and data_inicial > data_final:
        messages.error(request, 'A data inicial deve ser menor ou igual à data final.')
        data_inicial, data_final = data_final, data_inicial

    if data_inicial:
        queryset = queryset.filter(data__gte=data_inicial)
    if data_final:
        queryset = queryset.filter(data__lte=data_final)
    if orcamento_id:
        queryset = queryset.filter(orcamento_id=orcamento_id)

    return queryset.order_by('data', 'hora_inicio', 'criado_em'), data_inicial, data_final, orcamento_id


def _query_string(request):
    return request.META.get('QUERY_STRING', '')


def _sanitize_filename_part(value):
    value = re.sub(r'[<>:"/\\|?*\r\n\t]+', ' ', value or '')
    value = re.sub(r'\s+', ' ', value).strip(' .')
    return value or 'Sem preenchimento'


def _format_decimal_hours(value):
    total_minutes = int(round((value or 0) * 60))
    hours, minutes = divmod(total_minutes, 60)
    return f'{hours}h{minutes:02d}'


def _base_estimativas_queryset(user):
    return Estimativa.objects.prefetch_related('itens').filter(user=user)


def _filter_estimativas(request):
    queryset = _base_estimativas_queryset(request.user)
    data_inicial = _parse_date(request.GET.get('de'))
    data_final = _parse_date(request.GET.get('ate'))
    cliente = request.GET.get('cliente', '').strip()

    if data_inicial and data_final and data_inicial > data_final:
        messages.error(request, 'A data inicial deve ser menor ou igual à data final.')
        data_inicial, data_final = data_final, data_inicial

    if data_inicial:
        queryset = queryset.filter(criado_em__date__gte=data_inicial)
    if data_final:
        queryset = queryset.filter(criado_em__date__lte=data_final)
    if cliente:
        queryset = queryset.filter(cliente__icontains=cliente)

    return queryset, {
        'de': data_inicial.isoformat() if data_inicial else '',
        'ate': data_final.isoformat() if data_final else '',
        'cliente': cliente,
    }


def _agenda_selected_users(request):
    if not _user_is_gp(request.user):
        return [request.user]

    user_ids = []
    for value in request.GET.getlist('usuario'):
        if not value:
            continue
        try:
            user_ids.append(int(value))
        except (TypeError, ValueError) as exc:
            raise Http404 from exc

    if not user_ids:
        return []

    users = list(User.objects.filter(pk__in=user_ids).order_by('username'))
    if len(users) != len(set(user_ids)):
        raise Http404
    return users


def _agenda_selected_user(request):
    selected_users = _agenda_selected_users(request)
    return selected_users[0] if len(selected_users) == 1 else None


def _agenda_list_queryset(request, selected_users, month_start):
    if not selected_users:
        return _base_agenda_queryset().none()

    month_first_day, month_last_day = _month_bounds(month_start)
    return (
        _base_agenda_queryset()
        .filter(
            user__in=selected_users,
            data_inicio__lte=month_last_day,
            data_fim__gte=month_first_day,
        )
        .order_by('user__username', 'data_inicio', 'hora_inicio', 'titulo', 'pk')
    )


def _agenda_users_for_filter():
    return User.objects.order_by('username')


def _normalize_agenda_users(selected_users):
    if selected_users is None:
        return []
    if hasattr(selected_users, 'pk'):
        return [selected_users]
    return list(selected_users)


def _build_agenda_query(month_start, selected_users):
    selected_users = _normalize_agenda_users(selected_users)
    query = [('mes', month_start.strftime('%Y-%m'))]
    for selected_user in selected_users:
        query.append(('usuario', selected_user.pk))
    return query


def _build_agenda_url(month_start, selected_users):
    params = urlencode(_build_agenda_query(month_start, selected_users))
    return f"{reverse('horas:agenda')}?{params}" if params else reverse('horas:agenda')


def _build_agenda_create_url(month_start, selected_users, data=None):
    selected_users = _normalize_agenda_users(selected_users)
    query = [('mes', month_start.strftime('%Y-%m'))]
    if data is not None:
        query.insert(0, ('data', data.isoformat()))
    if len(selected_users) == 1:
        query.append(('usuario', selected_users[0].pk))
    return f"{reverse('horas:agenda_nova')}?{urlencode(query)}"


def _save_estimativa_formset(formset):
    itens = formset.save(commit=False)

    for deleted_item in formset.deleted_objects:
        deleted_item.delete()

    order = 1
    for item in itens:
        if not item.ordem:
            item.ordem = order
        item.save()
        order += 1


def _cell_position(ref):
    match = re.match(r'([A-Z]+)(\d+)', ref)
    if not match:
        return 0, 0
    col, row = match.groups()
    col_num = 0
    for char in col:
        col_num = col_num * 26 + ord(char) - ord('A') + 1
    return int(row), col_num


def _find_or_create_row(sheet_data, row_number):
    ns = {'m': XLSX_NS}
    for row in sheet_data.findall('m:row', ns):
        if int(row.attrib.get('r', '0')) == row_number:
            return row

    row = ET.Element(f'{{{XLSX_NS}}}row', {'r': str(row_number)})
    inserted = False
    for index, existing in enumerate(list(sheet_data)):
        if int(existing.attrib.get('r', '0')) > row_number:
            sheet_data.insert(index, row)
            inserted = True
            break
    if not inserted:
        sheet_data.append(row)
    return row


def _find_or_create_cell(row, ref):
    ns = {'m': XLSX_NS}
    for cell in row.findall('m:c', ns):
        if cell.attrib.get('r') == ref:
            return cell

    cell = ET.Element(f'{{{XLSX_NS}}}c', {'r': ref})
    _, target_col = _cell_position(ref)
    inserted = False
    for index, existing in enumerate(list(row)):
        _, existing_col = _cell_position(existing.attrib.get('r', ''))
        if existing_col > target_col:
            row.insert(index, cell)
            inserted = True
            break
    if not inserted:
        row.append(cell)
    return cell


def _set_cell_value(sheet_data, ref, value):
    row_number, _ = _cell_position(ref)
    row = _find_or_create_row(sheet_data, row_number)
    cell = _find_or_create_cell(row, ref)
    for child in list(cell):
        cell.remove(child)

    if isinstance(value, Decimal):
        value = float(value)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        cell.attrib.pop('t', None)
        ET.SubElement(cell, f'{{{XLSX_NS}}}v').text = str(value)
        return

    text = '' if value is None else str(value)
    cell.attrib['t'] = 'inlineStr'
    inline = ET.SubElement(cell, f'{{{XLSX_NS}}}is')
    text_node = ET.SubElement(inline, f'{{{XLSX_NS}}}t')
    if text.strip() != text:
        text_node.attrib['{http://www.w3.org/XML/1998/namespace}space'] = 'preserve'
    text_node.text = text


def _clear_cell(sheet_data, ref):
    row_number, _ = _cell_position(ref)
    row = _find_or_create_row(sheet_data, row_number)
    cell = _find_or_create_cell(row, ref)
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop('t', None)


def _excel_time_value(hours):
    return float((hours or Decimal('0')) / Decimal('24'))


def _normalize_estimativa_text(value):
    text = unicodedata.normalize('NFKD', value or '')
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'\s+', ' ', text).strip().casefold()


def _item_horas_estimadas(item):
    return (item.horas_analise or Decimal('0')) + (item.horas_atividade or Decimal('0'))


def _build_ehm_totals(itens):
    activity_by_resource = defaultdict(lambda: Decimal('0'))
    analysis_hours = Decimal('0')
    gp_hours = Decimal('0')

    for item in itens:
        activity_by_resource[_normalize_estimativa_text(item.recurso)] += item.horas_atividade or Decimal('0')
        analysis_hours += item.horas_analise or Decimal('0')
        gp_hours += item.horas_gp or Decimal('0')

    if gp_hours == 0:
        gp_hours = Decimal('1')

    rows = {
        23: (activity_by_resource['consultoria de implantacao'], Decimal('238')),
        24: (gp_hours, Decimal('250')),
        25: (activity_by_resource['desenvolvedor'], Decimal('255')),
        26: (activity_by_resource['analista de infraestrutura'], Decimal('290')),
        27: (activity_by_resource['consultoria especializada'], Decimal('300')),
        28: (analysis_hours, Decimal('238')),
        29: (Decimal('0'), Decimal('230')),
    }
    total_proposta = sum((hours * rate for row, (hours, rate) in rows.items() if row <= 28), Decimal('0'))
    total_sem_analise = total_proposta - (analysis_hours * Decimal('238'))
    total_horas = sum((hours for row, (hours, _) in rows.items() if row <= 28), Decimal('0'))
    return rows, total_horas, total_proposta, total_sem_analise


def _build_estimativa_xlsx(estimativa):
    template_path = Path(settings.BASE_DIR) / 'templates_xlsx' / 'estimativa_template.xlsx'
    if not template_path.exists():
        raise FileNotFoundError('Modelo de estimativa XLSX nao encontrado.')

    output = BytesIO()
    with zipfile.ZipFile(template_path, 'r') as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        workbook = ET.fromstring(source.read('xl/workbook.xml'))
        rels = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
        relmap = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels}
        sheet_path = None
        for sheet in workbook.findall(f'{{{XLSX_NS}}}sheets/{{{XLSX_NS}}}sheet'):
            if 'Escopo' in sheet.attrib.get('name', ''):
                target_path = relmap[sheet.attrib[f'{{{REL_NS}}}id']]
                sheet_path = 'xl/' + target_path.lstrip('/').replace('../', '')
                break

        if not sheet_path:
            raise ValueError('Aba de escopo nao encontrada no modelo XLSX.')

        sheet_root = ET.fromstring(source.read(sheet_path))
        sheet_data = sheet_root.find(f'{{{XLSX_NS}}}sheetData')
        _set_cell_value(sheet_data, 'C2', estimativa.cliente)
        _set_cell_value(sheet_data, 'C3', estimativa.solicitante)
        _set_cell_value(sheet_data, 'C5', estimativa.projeto)
        _set_cell_value(sheet_data, 'C6', estimativa.sistema)

        itens = list(estimativa.itens.all())
        ehm_rows, total_horas, total_proposta, total_sem_analise = _build_ehm_totals(itens)
        _set_cell_value(sheet_data, 'C19', _excel_time_value(total_horas))
        _set_cell_value(sheet_data, 'C20', total_proposta)
        _set_cell_value(sheet_data, 'C21', total_sem_analise)

        for row_number, (hours, rate) in ehm_rows.items():
            _set_cell_value(sheet_data, f'C{row_number}', _excel_time_value(hours))
            _set_cell_value(sheet_data, f'H{row_number}', hours * rate)

        for index in range(max(len(itens), 10)):
            row_number = 37 + index
            if index < len(itens):
                item = itens[index]
                horas_estimadas = _item_horas_estimadas(item)
                values = {
                    'A': index + 1,
                    'B': item.modulo_processo,
                    'C': item.recurso,
                    'D': item.escopo,
                    'E': _excel_time_value(item.horas_analise),
                    'F': _excel_time_value(item.horas_atividade),
                    'G': _excel_time_value(item.horas_gp),
                    'H': _excel_time_value(horas_estimadas),
                }
            else:
                values = {'A': '', 'B': '', 'C': '', 'D': '', 'E': '', 'F': '', 'G': '', 'H': ''}

            for column, value in values.items():
                ref = f'{column}{row_number}'
                if value == '':
                    _clear_cell(sheet_data, ref)
                else:
                    _set_cell_value(sheet_data, ref, value)

        sheet_bytes = ET.tostring(sheet_root, encoding='utf-8', xml_declaration=True)
        for item in source.infolist():
            data = sheet_bytes if item.filename == sheet_path else source.read(item.filename)
            target.writestr(item, data)

    output.seek(0)
    return output


ORCAMENTO_IMPORT_HEADERS = [
    'orcamento',
    'cliente',
    'chamado',
    'descricao',
]


def _xlsx_cell_text(cell, shared_strings):
    cell_type = cell.attrib.get('t')
    value = cell.find(f'{{{XLSX_NS}}}v')
    if cell_type == 'inlineStr':
        return ''.join(node.text or '' for node in cell.findall(f'.//{{{XLSX_NS}}}t'))
    if value is None or value.text is None:
        return ''
    if cell_type == 's':
        return shared_strings[int(value.text)]
    text = value.text
    if re.fullmatch(r'\d+\.0+', text):
        return text.split('.', 1)[0]
    return text


def _read_orcamentos_xlsx(arquivo):
    try:
        with zipfile.ZipFile(arquivo, 'r') as workbook:
            shared_strings = []
            if 'xl/sharedStrings.xml' in workbook.namelist():
                shared_root = ET.fromstring(workbook.read('xl/sharedStrings.xml'))
                shared_strings = [
                    ''.join(node.text or '' for node in item.findall(f'.//{{{XLSX_NS}}}t'))
                    for item in shared_root.findall(f'{{{XLSX_NS}}}si')
                ]

            workbook_root = ET.fromstring(workbook.read('xl/workbook.xml'))
            rels_root = ET.fromstring(workbook.read('xl/_rels/workbook.xml.rels'))
            relmap = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels_root}
            first_sheet = workbook_root.find(f'{{{XLSX_NS}}}sheets/{{{XLSX_NS}}}sheet')
            sheet_target = relmap[first_sheet.attrib[f'{{{REL_NS}}}id']]
            sheet_path = 'xl/' + sheet_target.lstrip('/').replace('../', '')
            sheet_root = ET.fromstring(workbook.read(sheet_path))
    except (AttributeError, IndexError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError('Não foi possível ler a planilha XLSX enviada.') from exc

    rows = []
    for row in sheet_root.findall(f'.//{{{XLSX_NS}}}sheetData/{{{XLSX_NS}}}row'):
        values = {}
        for cell in row.findall(f'{{{XLSX_NS}}}c'):
            _, column = _cell_position(cell.attrib.get('r', ''))
            values[column] = _xlsx_cell_text(cell, shared_strings).strip()
        if values:
            last_column = max(values)
            rows.append([values.get(column, '') for column in range(1, last_column + 1)])
    return rows


def _validate_orcamentos_import(rows):
    errors = []
    if not rows:
        return [], ['A planilha está vazia.']

    headers = [_normalize_estimativa_text(value) for value in rows[0]]
    if headers != ORCAMENTO_IMPORT_HEADERS:
        return [], [
            'Os cabeçalhos devem estar nesta ordem: orcamento, cliente, chamado, descricao.'
        ]

    existing_codes = set(Orcamento.objects.values_list('codigo', flat=True))
    imported_codes = set()
    orcamentos = []
    for row_number, row in enumerate(rows[1:], start=2):
        values = (row + ['', '', '', ''])[:4]
        if not any(values):
            continue
        codigo, codigo_cliente, numero_chamado, nome = (value.strip() for value in values)
        if not codigo:
            errors.append(f'Linha {row_number}: Código Orçamento é obrigatório.')
            continue
        for label, value in (
            ('Código Orçamento', codigo),
            ('Código Cliente', codigo_cliente),
            ('Número do Chamado', numero_chamado),
        ):
            if value and not value.isdigit():
                errors.append(f'Linha {row_number}: {label} deve conter somente números.')
        if codigo in existing_codes:
            errors.append(f'Linha {row_number}: o orçamento {codigo} já existe na base.')
        if codigo in imported_codes:
            errors.append(f'Linha {row_number}: o orçamento {codigo} está duplicado na planilha.')
        imported_codes.add(codigo)
        orcamentos.append(
            Orcamento(
                codigo=codigo,
                codigo_cliente=codigo_cliente,
                numero_chamado=numero_chamado,
                nome=nome,
            )
        )
    if not orcamentos and not errors:
        errors.append('A planilha não possui orçamentos para importar.')
    return orcamentos, errors


class SidebarContextMixin:
    def get_sidebar_total_today(self):
        total = sum(
            registro.total_horas
            for registro in _base_registros_queryset(self.request.user).filter(data=date.today())
        )
        return _format_decimal_hours(total)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['sidebar_total_today'] = self.get_sidebar_total_today()
        context['orcamentos_ativos'] = Orcamento.objects.filter(ativo=True).order_by('codigo')
        context['is_gp'] = _user_is_gp(self.request.user)
        context['pendencias_aprovacao_count'] = 0
        if context['is_gp']:
            context['pendencias_aprovacao_count'] = SolicitacaoHoras.objects.filter(
                orcamento__responsavel=self.request.user,
                situacao=SolicitacaoHoras.SITUACAO_AGUARDANDO,
            ).count()
        return context


class AuthenticatedViewMixin(LoginRequiredMixin):
    login_url = 'login'


class RequiredPasswordLoginView(LoginView):
    def get_success_url(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        if profile.must_change_password:
            return reverse('password_change_required')
        return super().get_success_url()


class RequiredPasswordChangeView(AuthenticatedViewMixin, TemplateView):
    template_name = 'registration/required_password_change.html'

    def get_form(self):
        return RequiredPasswordChangeForm(self.request.user, self.request.POST or None)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = kwargs.get('form') or self.get_form()
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        user = form.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.must_change_password = False
        profile.save(update_fields=['must_change_password'])
        update_session_auth_hash(request, user)
        messages.success(request, 'Senha alterada com sucesso.')
        return redirect(settings.LOGIN_REDIRECT_URL)


class GerenteProjetosRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _user_is_gp(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class DashboardView(AuthenticatedViewMixin, RedirectView):
    pattern_name = 'horas:timer'


class AgendaView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/agenda.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        month_start = _parse_month(self.request.GET.get('mes'))
        selected_users = _agenda_selected_users(self.request)
        selected_user = selected_users[0] if len(selected_users) == 1 else None
        atividades = list(_agenda_list_queryset(self.request, selected_users, month_start))
        for atividade in atividades:
            atividade.can_manage = _can_manage_agenda_activity(self.request.user, atividade)

        prev_month, next_month = _month_navigation(month_start)
        context['section'] = 'agenda'
        context['is_gp'] = _user_is_gp(self.request.user)
        context['agenda_compare_mode'] = len(selected_users) > 1
        context['agenda_weeks'] = (
            _build_agenda_calendar(month_start, atividades, selected_users) if selected_users else []
        )
        context['agenda_atividades'] = atividades
        context['agenda_filters'] = _agenda_filter_context(month_start, selected_users)
        context['agenda_url'] = _build_agenda_url(month_start, selected_users)
        context['agenda_prev_url'] = _build_agenda_url(prev_month, selected_users)
        context['agenda_next_url'] = _build_agenda_url(next_month, selected_users)
        context['agenda_create_url'] = _build_agenda_create_url(month_start, selected_users)
        context['agenda_users'] = _agenda_users_for_filter() if context['is_gp'] else []
        context['selected_users'] = selected_users
        context['selected_user_ids'] = {selected_user.pk for selected_user in selected_users}
        context['selected_user'] = selected_user
        context['show_agenda_empty_filter'] = context['is_gp'] and not selected_users
        return context


class AgendaBaseFormView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/agenda_form.html'

    def dispatch(self, request, *args, **kwargs):
        if not hasattr(self, 'month_start'):
            self.month_start = _parse_month(request.GET.get('mes'))
        if not hasattr(self, 'selected_user'):
            self.selected_user = _agenda_selected_user(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'agenda'
        context['form'] = kwargs.get('form')
        context['atividade'] = getattr(self, 'atividade', None)
        context['is_gp'] = _user_is_gp(self.request.user)
        context['agenda_back_url'] = _build_agenda_url(self.month_start, self.selected_user)
        return context


class AgendaCreateView(AgendaBaseFormView):
    def get_context_data(self, **kwargs):
        form = kwargs.pop('form', None)
        if form is None:
            initial = {}
            data_param = _parse_date(self.request.GET.get('data'))
            if data_param:
                initial['data_inicio'] = data_param
                initial['data_fim'] = data_param
            usuario_param = self.request.GET.get('usuario')
            if usuario_param:
                try:
                    usuario_id = int(usuario_param)
                except (TypeError, ValueError):
                    usuario_id = None
                if usuario_id and User.objects.filter(pk=usuario_id).exists():
                    initial['user'] = usuario_id
            form = AgendaAtividadeForm(initial=initial, current_user=self.request.user)
        return super().get_context_data(form=form, **kwargs)

    def post(self, request, *args, **kwargs):
        form = AgendaAtividadeForm(request.POST, current_user=request.user)
        if form.is_valid():
            atividade = form.save(commit=False)
            atividade.criado_por = request.user
            if not _user_is_gp(request.user):
                atividade.user = request.user
            atividade.save()
            messages.success(request, 'Atividade adicionada na agenda com sucesso.')
            return redirect(
                _build_agenda_url(
                    atividade.data_inicio.replace(day=1),
                    atividade.user if _user_is_gp(request.user) else request.user,
                )
            )

        messages.error(request, 'Corrija os campos destacados antes de salvar a atividade.')
        return self.render_to_response(self.get_context_data(form=form))


class AgendaUpdateView(AgendaBaseFormView):
    def dispatch(self, request, *args, **kwargs):
        self.atividade = get_object_or_404(_agenda_manage_queryset(request.user), pk=kwargs['pk'])
        self.selected_user = self.atividade.user if _user_is_gp(request.user) else request.user
        self.month_start = self.atividade.data_inicio.replace(day=1)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        form = kwargs.pop('form', None) or AgendaAtividadeForm(instance=self.atividade, current_user=self.request.user)
        return super().get_context_data(form=form, **kwargs)

    def post(self, request, *args, **kwargs):
        form = AgendaAtividadeForm(request.POST, instance=self.atividade, current_user=request.user)
        if form.is_valid():
            atividade = form.save(commit=False)
            if not _user_is_gp(request.user):
                atividade.user = request.user
            atividade.save()
            messages.success(request, 'Atividade da agenda atualizada com sucesso.')
            return redirect(_build_agenda_url(atividade.data_inicio.replace(day=1), atividade.user))

        messages.error(request, 'Corrija os campos destacados antes de salvar a atividade.')
        return self.render_to_response(self.get_context_data(form=form))


@method_decorator(login_required(login_url='login'), name='dispatch')
class AgendaDeleteView(View):
    def post(self, request, pk):
        atividade = get_object_or_404(_agenda_manage_queryset(request.user), pk=pk)
        month_start = atividade.data_inicio.replace(day=1)
        selected_user = atividade.user
        atividade.delete()
        messages.success(request, 'Atividade removida da agenda.')
        return redirect(_build_agenda_url(month_start, selected_user if _user_is_gp(request.user) else request.user))


class TimerView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/timer.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'timer'
        form = kwargs.get('form')
        if form is None:
            initial = {}
            data_inicial = _parse_date(self.request.GET.get('data'))
            orcamento_id = self.request.GET.get('orcamento')
            if data_inicial:
                initial['data'] = data_inicial
            if orcamento_id and Orcamento.objects.filter(pk=orcamento_id).exists():
                initial['orcamento'] = orcamento_id
            form = RegistroForm(initial=initial)
        context['form'] = form
        context['extra_rows'] = kwargs.get('extra_rows') or []
        return context

    def post(self, request, *args, **kwargs):
        submission_mode = request.POST.get('submission_mode', 'manual')
        if submission_mode == 'timer':
            form = RegistroForm(request.POST)
            if form.is_valid():
                registro = form.save(commit=False)
                registro.user = request.user
                try:
                    registro.save()
                except ValidationError as exc:
                    _add_model_validation_to_form(form, exc)
                else:
                    messages.success(request, 'Registro salvo com sucesso.')
                    return redirect('horas:timer')

            messages.error(request, 'Corrija os campos destacados antes de salvar.')
            return self.render_to_response(self.get_context_data(form=form))

        rows = _build_timer_rows_from_post(request)
        row_forms = []
        extra_rows = rows[1:]
        common_data = {
            'data': request.POST.get('data', ''),
            'orcamento': request.POST.get('orcamento', ''),
            'fase': request.POST.get('fase', ''),
        }

        for row in rows:
            row_forms.append(
                RegistroForm(
                    {
                        **common_data,
                        'hora_inicio': row['hora_inicio'],
                        'hora_fim': row['hora_fim'],
                        'descricao': row['descricao'],
                    }
                )
            )

        if row_forms and all(form.is_valid() for form in row_forms):
            try:
                with transaction.atomic():
                    for form in row_forms:
                        registro = form.save(commit=False)
                        registro.user = request.user
                        registro.save()
            except ValidationError as exc:
                _add_model_validation_to_form(row_forms[0], exc)
            else:
                quantidade = len(row_forms)
                messages.success(
                    request,
                    f'{quantidade} registro{"s" if quantidade != 1 else ""} salvo{"s" if quantidade != 1 else ""} com sucesso.',
                )
                return redirect('horas:timer')

        form = row_forms[0] if row_forms else RegistroForm(request.POST)
        messages.error(request, 'Corrija os campos destacados antes de salvar.')
        return self.render_to_response(self.get_context_data(form=form, extra_rows=extra_rows))


class RegistrosView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/registros.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        registros, data_inicial, data_final, orcamento_id = _filter_registros(self.request)
        context['section'] = 'registros'
        context['registros'] = registros
        context['filtros'] = {
            'de': data_inicial.isoformat() if data_inicial else '',
            'ate': data_final.isoformat() if data_final else '',
            'orcamento': orcamento_id or '',
        }
        context['query_string'] = _query_string(self.request)
        return context


class EstimativasView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/estimativas.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        estimativas_queryset, filtros = _filter_estimativas(self.request)
        estimativas = list(estimativas_queryset)
        context['section'] = 'estimativas'
        context['estimativas'] = estimativas
        context['filtros'] = filtros
        context['total_estimativas'] = len(estimativas)
        return context


class EstimativaCreateView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/estimativa_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'estimativas'
        context['estimativa'] = None
        context['form'] = kwargs.get('form') or EstimativaForm()
        context['formset'] = kwargs.get('formset') or EstimativaItemCreateFormSet()
        return context

    def post(self, request, *args, **kwargs):
        form = EstimativaForm(request.POST)
        formset = EstimativaItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                estimativa = form.save(commit=False)
                estimativa.user = request.user
                estimativa.save()
                formset.instance = estimativa
                _save_estimativa_formset(formset)
            messages.success(request, 'Estimativa criada com sucesso.')
            return redirect('horas:estimativas')

        messages.error(request, 'Corrija os campos destacados antes de salvar a estimativa.')
        return self.render_to_response(self.get_context_data(form=form, formset=formset))


class EstimativaUpdateView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/estimativa_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.estimativa = get_object_or_404(_base_estimativas_queryset(request.user), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'estimativas'
        context['estimativa'] = self.estimativa
        context['form'] = kwargs.get('form') or EstimativaForm(instance=self.estimativa)
        context['formset'] = kwargs.get('formset') or EstimativaItemFormSet(instance=self.estimativa)
        return context

    def post(self, request, *args, **kwargs):
        form = EstimativaForm(request.POST, instance=self.estimativa)
        formset = EstimativaItemFormSet(request.POST, instance=self.estimativa)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                form.save()
                _save_estimativa_formset(formset)
            messages.success(request, 'Estimativa atualizada com sucesso.')
            return redirect('horas:estimativas')

        messages.error(request, 'Corrija os campos destacados antes de salvar a estimativa.')
        return self.render_to_response(self.get_context_data(form=form, formset=formset))


@method_decorator(login_required(login_url='login'), name='dispatch')
class EstimativaDeleteView(View):
    def post(self, request, pk):
        estimativa = get_object_or_404(_base_estimativas_queryset(request.user), pk=pk)
        estimativa.delete()
        messages.success(request, 'Estimativa removida.')
        return redirect('horas:estimativas')


class RegistroUpdateView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/registro_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.registro = get_object_or_404(_base_registros_queryset(request.user), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'registros'
        context['registro'] = self.registro
        context['form'] = kwargs.get('form') or RegistroForm(instance=self.registro)
        context['query_string'] = _query_string(self.request)
        return context

    def post(self, request, *args, **kwargs):
        form = RegistroForm(request.POST, instance=self.registro)
        if form.is_valid():
            registro = form.save(commit=False)
            registro.user = request.user
            try:
                registro.save()
            except ValidationError as exc:
                _add_model_validation_to_form(form, exc)
            else:
                messages.success(request, 'Registro atualizado com sucesso.')
                destino = reverse('horas:registros')
                query = _query_string(request)
                if query:
                    destino = f'{destino}?{query}'
                return redirect(destino)

        messages.error(request, 'Corrija os campos destacados antes de salvar.')
        return self.render_to_response(self.get_context_data(form=form))


@method_decorator(login_required(login_url='login'), name='dispatch')
class RegistroDeleteView(View):
    def post(self, request, pk):
        registro = get_object_or_404(_base_registros_queryset(request.user), pk=pk)
        registro.delete()
        messages.success(request, 'Registro removido.')
        query = _query_string(request)
        destino = reverse('horas:registros')
        if query:
            destino = f'{destino}?{query}'
        return redirect(destino)


@method_decorator(login_required(login_url='login'), name='dispatch')
class RegistroProcessarView(View):
    def post(self, request, pk):
        registro = get_object_or_404(_base_registros_queryset(request.user), pk=pk)
        if registro.processado == Registro.PROCESSADO_SIM:
            registro.processado = Registro.PROCESSADO_NAO
            messages.success(request, 'Registro desmarcado como processado.')
        else:
            registro.processado = Registro.PROCESSADO_SIM
            messages.success(request, 'Registro marcado como processado.')
        registro.save(update_fields=['processado', 'atualizado_em'])

        query = _query_string(request)
        destino = reverse('horas:registros')
        if query:
            destino = f'{destino}?{query}'
        return redirect(destino)


class ResumoView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/resumo.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        registros, data_inicial, data_final, _ = _filter_registros(self.request)
        registros_list = list(registros)
        total_horas = sum(registro.total_horas for registro in registros_list)
        dias_trabalhados = len({registro.data for registro in registros_list})
        media_diaria = total_horas / dias_trabalhados if dias_trabalhados else 0

        por_orcamento = defaultdict(lambda: {'count': 0, 'hours': 0, 'codigo': '—', 'nome': ''})
        for registro in registros_list:
            item = por_orcamento[registro.orcamento_id]
            item['codigo'] = registro.orcamento.codigo
            item['nome'] = registro.orcamento.nome
            item['count'] += 1
            item['hours'] += registro.total_horas

        detalhes = sorted(
            por_orcamento.values(),
            key=lambda item: item['hours'],
            reverse=True,
        )
        for item in detalhes:
            item['total_formatado'] = _format_decimal_hours(item['hours'])

        context['section'] = 'resumo'
        context['stats'] = [
            ('Total no período', _format_decimal_hours(total_horas)),
            ('Registros', len(registros_list)),
            ('Dias trabalhados', dias_trabalhados),
            ('Média por dia', _format_decimal_hours(media_diaria)),
        ]
        context['detalhes_orcamento'] = detalhes
        context['filtros'] = {
            'de': data_inicial.isoformat() if data_inicial else '',
            'ate': data_final.isoformat() if data_final else '',
        }
        return context


class SolicitacoesHorasView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/solicitacoes_horas.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'solicitacoes_horas'
        context['form'] = kwargs.get('form') or SolicitacaoHorasForm()
        context['solicitacoes'] = SolicitacaoHoras.objects.select_related(
            'orcamento',
            'orcamento__responsavel',
            'decidido_por',
        ).filter(solicitante=self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        form = SolicitacaoHorasForm(request.POST)
        if form.is_valid():
            solicitacao = form.save(commit=False)
            solicitacao.solicitante = request.user
            solicitacao.save()
            messages.success(
                request,
                f'Solicitação {solicitacao.numero_solicitacao} enviada para aprovação.',
            )
            return redirect('horas:solicitacoes_horas')

        messages.error(request, 'Corrija os campos destacados antes de enviar a solicitação.')
        return self.render_to_response(self.get_context_data(form=form))


class SolicitacoesHorasPendentesView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/solicitacoes_horas_pendentes.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'solicitacoes_horas_pendentes'
        solicitacoes = SolicitacaoHoras.objects.select_related(
            'solicitante',
            'orcamento',
            'decidido_por',
        ).filter(orcamento__responsavel=self.request.user)
        context['solicitacoes_pendentes'] = solicitacoes.filter(
            situacao=SolicitacaoHoras.SITUACAO_AGUARDANDO
        )
        context['solicitacoes_processadas'] = solicitacoes.exclude(
            situacao=SolicitacaoHoras.SITUACAO_AGUARDANDO
        )
        return context


class SolicitacaoHorasDecisaoView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    View,
):
    def post(self, request, pk):
        decisao = request.POST.get('decisao')
        motivo_reprovacao = request.POST.get('motivo_reprovacao', '').strip()

        with transaction.atomic():
            solicitacao = get_object_or_404(
                SolicitacaoHoras.objects.select_for_update().select_related('orcamento'),
                pk=pk,
                orcamento__responsavel=request.user,
            )
            if solicitacao.situacao != SolicitacaoHoras.SITUACAO_AGUARDANDO:
                messages.warning(request, 'Esta solicitação já foi processada.')
                return redirect('horas:solicitacoes_horas_pendentes')

            if decisao == 'aprovar':
                orcamento = Orcamento.objects.select_for_update().get(pk=solicitacao.orcamento_id)
                orcamento.horas_adicionais += solicitacao.quantidade_horas
                orcamento.save(update_fields=['horas_adicionais'])
                solicitacao.situacao = SolicitacaoHoras.SITUACAO_APROVADO
                solicitacao.motivo_reprovacao = ''
                mensagem = 'Solicitação aprovada e horas adicionais liberadas.'
            elif decisao == 'reprovar':
                if not motivo_reprovacao:
                    messages.error(request, 'Informe o motivo para reprovar a solicitação.')
                    return redirect('horas:solicitacoes_horas_pendentes')
                solicitacao.situacao = SolicitacaoHoras.SITUACAO_REPROVADO
                solicitacao.motivo_reprovacao = motivo_reprovacao
                mensagem = 'Solicitação reprovada.'
            else:
                messages.error(request, 'Decisão inválida.')
                return redirect('horas:solicitacoes_horas_pendentes')

            solicitacao.decidido_por = request.user
            solicitacao.decidido_em = timezone.now()
            solicitacao.save(
                update_fields=[
                    'situacao',
                    'motivo_reprovacao',
                    'decidido_por',
                    'decidido_em',
                ]
            )

        messages.success(request, mensagem)
        return redirect('horas:solicitacoes_horas_pendentes')


class OrcamentosView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/orcamentos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'orcamentos'
        context['form'] = kwargs.get('form') or OrcamentoForm()
        context['import_form'] = kwargs.get('import_form') or OrcamentoImportForm()
        context['import_errors'] = kwargs.get('import_errors') or []
        context['orcamentos'] = Orcamento.objects.select_related('responsavel').order_by('codigo')
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('action') == 'importar':
            import_form = OrcamentoImportForm(request.POST, request.FILES)
            if import_form.is_valid():
                try:
                    rows = _read_orcamentos_xlsx(import_form.cleaned_data['arquivo'])
                    orcamentos, import_errors = _validate_orcamentos_import(rows)
                except ValueError as exc:
                    import_errors = [str(exc)]
                    orcamentos = []
                if not import_errors:
                    for orcamento in orcamentos:
                        orcamento.responsavel = request.user
                    with transaction.atomic():
                        Orcamento.objects.bulk_create(orcamentos)
                    messages.success(request, f'{len(orcamentos)} orçamento(s) importado(s) com sucesso.')
                    return redirect('horas:orcamentos')
                messages.error(request, 'A planilha possui erros e nenhum orçamento foi importado.')
                return self.render_to_response(
                    self.get_context_data(import_form=import_form, import_errors=import_errors)
                )
            messages.error(request, 'Selecione uma planilha XLSX válida.')
            return self.render_to_response(self.get_context_data(import_form=import_form))

        form = OrcamentoForm(request.POST)
        if form.is_valid():
            orcamento = form.save(commit=False)
            orcamento.responsavel = request.user
            orcamento.save()
            messages.success(request, 'Orçamento adicionado com sucesso.')
            return redirect('horas:orcamentos')

        messages.error(request, 'Corrija os campos destacados antes de adicionar o orçamento.')
        return self.render_to_response(self.get_context_data(form=form))


class OrcamentoUpdateView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/orcamento_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.orcamento = get_object_or_404(
            Orcamento.objects.select_related('responsavel'),
            pk=kwargs['pk'],
            responsavel=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'orcamentos'
        context['orcamento'] = self.orcamento
        context['form'] = kwargs.get('form') or OrcamentoForm(instance=self.orcamento)
        return context

    def post(self, request, *args, **kwargs):
        form = OrcamentoForm(request.POST, instance=self.orcamento)
        if form.is_valid():
            form.save()
            messages.success(request, 'Orçamento atualizado com sucesso.')
            return redirect('horas:orcamentos')

        messages.error(request, 'Corrija os campos destacados antes de salvar o orçamento.')
        return self.render_to_response(self.get_context_data(form=form))


class FasesView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/fases.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'fases'
        context['form'] = kwargs.get('form') or FaseForm()
        context['fases'] = Fase.objects.order_by('codigo')
        return context

    def post(self, request, *args, **kwargs):
        form = FaseForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Fase adicionada com sucesso.')
            return redirect('horas:fases')

        messages.error(request, 'Corrija os campos destacados antes de adicionar a fase.')
        return self.render_to_response(self.get_context_data(form=form))


class OrcamentoDeleteView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, View):
    def post(self, request, pk):
        orcamento = get_object_or_404(Orcamento, pk=pk, responsavel=request.user)
        try:
            orcamento.delete()
            messages.success(request, 'Orçamento removido.')
        except ProtectedError:
            orcamento.ativo = False
            orcamento.save(update_fields=['ativo'])
            messages.warning(
                request,
                'O orçamento possui registros vinculados e foi desativado em vez de removido.',
            )
        return redirect('horas:orcamentos')


class FaseDeleteView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, View):
    def post(self, request, pk):
        fase = get_object_or_404(Fase, pk=pk)
        fase.delete()
        messages.success(request, 'Fase removida.')
        return redirect('horas:fases')


@login_required(login_url='login')
def exportar_registros_csv(request):
    registros, _, _, _ = _filter_registros(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="apontamento_{date.today().isoformat()}.csv"'
    )
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Data', 'Hora Inicio', 'Hora Fim', 'Total', 'Codigo Orcamento', 'Descricao'])
    for registro in registros:
        writer.writerow(
            [
                registro.data.strftime('%d/%m/%Y'),
                registro.hora_inicio.strftime('%H:%M'),
                registro.hora_fim.strftime('%H:%M'),
                registro.total_formatado,
                registro.orcamento.codigo,
                registro.descricao,
            ]
        )
    return response


@login_required(login_url='login')
def exportar_estimativa_xlsx(request, pk):
    estimativa = get_object_or_404(_base_estimativas_queryset(request.user), pk=pk)
    try:
        arquivo = _build_estimativa_xlsx(estimativa)
    except (FileNotFoundError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect('horas:estimativas')

    cliente = _sanitize_filename_part(estimativa.cliente)
    projeto = _sanitize_filename_part(estimativa.projeto)
    filename = f'EHM DEVS - {cliente} - {projeto}.xlsx'
    response = HttpResponse(
        arquivo.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
