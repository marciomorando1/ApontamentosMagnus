import csv
import calendar
import logging
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlencode, urlunparse
from xml.etree import ElementTree as ET

import requests
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
from zeep import Client as ZeepClient
from zeep.helpers import serialize_object
from zeep.transports import Transport

from .forms import (
    AgendaAtividadeForm,
    ClienteEditForm,
    ClienteForm,
    ClienteImportForm,
    ConfiguracaoSistemaForm,
    EstimativaForm,
    EstimativaItemCreateFormSet,
    EstimativaItemFormSet,
    FaseForm,
    FolgaFeriadoForm,
    DurationField,
    OrcamentoForm,
    OrcamentoImportForm,
    REGISTRO_DESCRICAO_MAX_LENGTH,
    RegistroForm,
    RequiredPasswordChangeForm,
    ServicoForm,
    SolicitacaoHorasForm,
)
from .models import (
    AgendaAtividade,
    Cliente,
    ConfiguracaoSistema,
    Estimativa,
    Fase,
    FolgaFeriado,
    Orcamento,
    OrcamentoServico,
    Registro,
    Servico,
    SolicitacaoHoras,
    UserProfile,
)


XLSX_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('', XLSX_NS)
ET.register_namespace('r', REL_NS)
User = get_user_model()
logger = logging.getLogger(__name__)
ERP_CLIENTES_WSDL_URL = 'http://wsadmteste.magnus.com.br/g5-senior-services/sapiens_Synccom_magnus_rat?wsdl'
ERP_CLIENTES_TIMEOUT = 30
ERP_CLIENTES_INTERNAL_BASE_URL = 'http://srvsnr01:8088'
ERP_CLIENTES_PUBLIC_BASE_URL = 'http://wsadmteste.magnus.com.br:8088'
ERP_CLIENTES_SERVICE_PATH = '/g5-senior-services/sapiens_Synccom_magnus_rat'
MONTH_LABELS = [
    '',
    'Janeiro',
    'Fevereiro',
    'MarÃ§o',
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


def _base_registros_queryset(user, *, include_all_users=False):
    queryset = Registro.objects.select_related('user', 'user__profile', 'orcamento', 'fase', 'servico')
    if include_all_users:
        return queryset
    return queryset.filter(user=user)


def _user_is_admin(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile.is_administrador


def _user_is_gp(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile.is_gerente_projetos or profile.is_administrador


def _user_can_export_csv(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile.exportacsv


def _base_agenda_queryset():
    return AgendaAtividade.objects.select_related('user', 'criado_por', 'orcamento', 'orcamento__pmo', 'servico')


def _base_folga_feriado_queryset():
    return FolgaFeriado.objects.select_related('user', 'criado_por')


def _folga_feriado_manage_queryset(user):
    return _base_folga_feriado_queryset().filter(criado_por=user)

def _agenda_manage_queryset(user):
    if _user_is_admin(user):
        return _base_agenda_queryset()
    if _user_is_gp(user):
        return _base_agenda_queryset().filter(Q(user=user) | Q(criado_por=user))
    return _base_agenda_queryset().filter(user=user, criado_por=user)


def _can_manage_agenda_activity(user, atividade):
    if _user_is_admin(user):
        return True
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


def _is_weekday(day):
    return day.weekday() < 5


def _build_agenda_calendar(month_start, atividades, selected_users=None, folgas_feriados=None):
    first_day, last_day = _month_bounds(month_start)
    cal = calendar.Calendar(firstweekday=6)
    atividades_por_dia = defaultdict(list)
    folgas_por_dia = defaultdict(list)
    selected_users = list(selected_users or [])

    for atividade in atividades:
        current_day = max(atividade.data_inicio, first_day)
        final_day = min(atividade.data_fim, last_day)
        while current_day <= final_day:
            if _is_weekday(current_day):
                atividades_por_dia[current_day].append(atividade)
            current_day += timedelta(days=1)

    for folga in folgas_feriados or []:
        folgas_por_dia[folga.data].append(folga)

    for items in atividades_por_dia.values():
        items.sort(
            key=lambda atividade: (
                atividade.hora_inicio is None,
                atividade.hora_inicio,
                atividade.titulo,
                atividade.pk,
            )
        )
    for items in folgas_por_dia.values():
        items.sort(key=lambda folga: (folga.descricao, folga.pk))

    weeks = []
    for week in cal.monthdatescalendar(first_day.year, first_day.month):
        week_days = []
        for day in week:
            items = list(atividades_por_dia.get(day, []))
            folgas_items = list(folgas_por_dia.get(day, []))
            user_groups = []
            if selected_users:
                for selected_user in selected_users:
                    user_items = [
                        atividade for atividade in items if atividade.user_id == selected_user.pk
                    ]
                    user_folgas = [
                        folga
                        for folga in folgas_items
                        if folga.abrangencia_todos or folga.user_id == selected_user.pk
                    ]
                    user_groups.append(
                        {
                            'user': selected_user,
                            'atividades': user_items,
                            'folgas_feriados': user_folgas,
                            'total_items': len(user_items) + len(user_folgas),
                        }
                    )
            week_days.append(
                {
                    'date': day,
                    'in_month': day.month == first_day.month,
                    'is_today': day == date.today(),
                    'atividades': items,
                    'folgas_feriados': folgas_items,
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


def _filter_registros(request, *, allow_usuario_filter=False):
    can_filter_usuario = allow_usuario_filter and _user_can_export_csv(request.user)
    queryset = _base_registros_queryset(request.user, include_all_users=can_filter_usuario)
    data_inicial = _parse_date(request.GET.get('de'))
    data_final = _parse_date(request.GET.get('ate'))
    orcamento_id = request.GET.get('orcamento')
    usuario_id = request.GET.get('usuario') if can_filter_usuario else ''

    if not data_inicial and not data_final:
        data_inicial = date.today()
        data_final = date.today()

    if data_inicial and data_final and data_inicial > data_final:
        messages.error(request, 'A data inicial deve ser menor ou igual Ã  data final.')
        data_inicial, data_final = data_final, data_inicial

    if data_inicial:
        queryset = queryset.filter(data__gte=data_inicial)
    if data_final:
        queryset = queryset.filter(data__lte=data_final)
    if orcamento_id:
        queryset = queryset.filter(orcamento_id=orcamento_id)
    if usuario_id:
        queryset = queryset.filter(user_id=usuario_id)

    return (
        queryset.order_by('data', 'hora_inicio', 'criado_em'),
        data_inicial,
        data_final,
        orcamento_id,
        usuario_id,
    )


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
        messages.error(request, 'A data inicial deve ser menor ou igual Ã  data final.')
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


def _agenda_folga_feriado_queryset(request, selected_users, month_start):
    if not selected_users:
        return _base_folga_feriado_queryset().none()

    month_first_day, month_last_day = _month_bounds(month_start)
    return (
        _base_folga_feriado_queryset()
        .filter(
            Q(user__in=selected_users) | Q(abrangencia_todos=True),
            data__gte=month_first_day,
            data__lte=month_last_day,
        )
        .order_by('-abrangencia_todos', 'user__username', 'data', 'descricao', 'pk')
    )

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
    'qtd_horas',
    'pmo',
]

CLIENTE_IMPORT_HEADERS = [
    'codigo',
    'nome',
]


XLSX_BUILTIN_DURATION_FORMAT_IDS = {'20', '21', '45', '46', '47'}


def _xlsx_duration_style_ids(workbook):
    if 'xl/styles.xml' not in workbook.namelist():
        return set()

    try:
        styles_root = ET.fromstring(workbook.read('xl/styles.xml'))
    except ET.ParseError:
        return set()

    custom_formats = {}
    for num_fmt in styles_root.findall(f'{{{XLSX_NS}}}numFmts/{{{XLSX_NS}}}numFmt'):
        num_fmt_id = num_fmt.attrib.get('numFmtId')
        format_code = (num_fmt.attrib.get('formatCode') or '').lower()
        if num_fmt_id:
            custom_formats[num_fmt_id] = format_code

    duration_style_ids = set()
    for index, xf in enumerate(styles_root.findall(f'{{{XLSX_NS}}}cellXfs/{{{XLSX_NS}}}xf')):
        num_fmt_id = xf.attrib.get('numFmtId')
        format_code = custom_formats.get(num_fmt_id, '')
        if num_fmt_id in XLSX_BUILTIN_DURATION_FORMAT_IDS or '[h]' in format_code:
            duration_style_ids.add(str(index))
    return duration_style_ids


def _xlsx_cell_text(cell, shared_strings, duration_style_ids=None):
    cell_type = cell.attrib.get('t')
    value = cell.find(f'{{{XLSX_NS}}}v')
    if cell_type == 'inlineStr':
        return ''.join(node.text or '' for node in cell.findall(f'.//{{{XLSX_NS}}}t'))
    if value is None or value.text is None:
        return ''
    if cell_type == 's':
        return shared_strings[int(value.text)]
    text = value.text
    if duration_style_ids and cell.attrib.get('s') in duration_style_ids:
        try:
            total_minutes = int(round(Decimal(text) * Decimal('24') * Decimal('60')))
        except InvalidOperation:
            pass
        else:
            hours, minutes = divmod(total_minutes, 60)
            return f'{hours}:{minutes:02d}'
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

            duration_style_ids = _xlsx_duration_style_ids(workbook)
            workbook_root = ET.fromstring(workbook.read('xl/workbook.xml'))
            rels_root = ET.fromstring(workbook.read('xl/_rels/workbook.xml.rels'))
            relmap = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels_root}
            first_sheet = workbook_root.find(f'{{{XLSX_NS}}}sheets/{{{XLSX_NS}}}sheet')
            sheet_target = relmap[first_sheet.attrib[f'{{{REL_NS}}}id']]
            sheet_path = 'xl/' + sheet_target.lstrip('/').replace('../', '')
            sheet_root = ET.fromstring(workbook.read(sheet_path))
    except (AttributeError, IndexError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError('NÃ£o foi possÃ­vel ler a planilha XLSX enviada.') from exc

    rows = []
    for row in sheet_root.findall(f'.//{{{XLSX_NS}}}sheetData/{{{XLSX_NS}}}row'):
        values = {}
        for cell in row.findall(f'{{{XLSX_NS}}}c'):
            _, column = _cell_position(cell.attrib.get('r', ''))
            values[column] = _xlsx_cell_text(cell, shared_strings, duration_style_ids).strip()
        if values:
            last_column = max(values)
            rows.append([values.get(column, '') for column in range(1, last_column + 1)])
    return rows


def _find_pmo_user(value):
    lookup = _normalize_estimativa_text(value)
    if not lookup:
        return None

    for user in User.objects.filter(profile__is_pmo=True).select_related('profile').order_by('username'):
        user_keys = [
            _normalize_estimativa_text(user.username),
            _normalize_estimativa_text(user.get_full_name()),
        ]
        if lookup in user_keys:
            return user
    return None


def _clean_orcamento_import_hours(value, field):
    text = value.strip()
    if ':' not in text:
        try:
            number = Decimal(text.replace(',', '.'))
        except InvalidOperation:
            pass
        else:
            if Decimal('0') < number <= Decimal('1') and ('.' in text or ',' in text):
                return number * Decimal('24')
    return field.clean(value)



def _validate_clientes_import(rows):
    errors = []
    if not rows:
        return {}, ['A planilha esta vazia.']

    headers = [_normalize_estimativa_text(value) for value in rows[0]]
    while headers and not headers[-1]:
        headers.pop()
    if headers != CLIENTE_IMPORT_HEADERS:
        return {}, ['Os cabecalhos devem estar nesta ordem: codigo, nome.']

    clientes = {}
    for row_number, row in enumerate(rows[1:], start=2):
        values = (row + ['', ''])[:2]
        if not any(values):
            continue
        codigo, nome = (value.strip() for value in values)
        if not codigo:
            errors.append(f'Linha {row_number}: Codigo do cliente e obrigatorio.')
            continue
        if not codigo.isdigit():
            errors.append(f'Linha {row_number}: Codigo do cliente deve conter somente numeros.')
        if not nome:
            errors.append(f'Linha {row_number}: Nome do cliente e obrigatorio.')
        if codigo in clientes and clientes[codigo] != nome:
            errors.append(f'Linha {row_number}: o cliente {codigo} esta duplicado com nomes diferentes.')
        clientes[codigo] = nome

    if not clientes and not errors:
        errors.append('A planilha nao possui clientes para importar.')
    return clientes, errors


def _erp_value_to_text(value):
    if value is None:
        return ''
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _get_erp_field(record, *field_names):
    if not isinstance(record, dict):
        return None
    fields_by_name = {str(key).lower(): value for key, value in record.items()}
    for field_name in field_names:
        value = fields_by_name.get(field_name.lower())
        if value is not None:
            return value
    return None


def _iter_erp_cliente_records(value):
    if isinstance(value, dict):
        if _get_erp_field(value, 'codcli', 'codCli') is not None and _get_erp_field(value, 'nomCli', 'nomcli') is not None:
            yield value
        for child in value.values():
            yield from _iter_erp_cliente_records(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_erp_cliente_records(child)


def _iter_erp_orcamento_records(value):
    if isinstance(value, dict):
        if _get_erp_field(value, 'numOrc', 'numorc') is not None:
            yield value
        for child in value.values():
            yield from _iter_erp_orcamento_records(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_erp_orcamento_records(child)


def _iter_erp_servico_records(value):
    if isinstance(value, dict):
        if _get_erp_field(value, 'codSer', 'codser') is not None:
            yield value
        for child in value.values():
            yield from _iter_erp_servico_records(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_erp_servico_records(child)


def _iter_erp_servico_orcamento_records(value):
    if isinstance(value, dict):
        if _get_erp_field(value, 'numOrc', 'numorc') is not None and _get_erp_field(value, 'codSer', 'codser') is not None:
            yield value
        for child in value.values():
            yield from _iter_erp_servico_orcamento_records(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_erp_servico_orcamento_records(child)


def _extract_clientes_erp_data(response):
    clientes = {}
    errors = []
    serialized_response = serialize_object(response)
    erro_execucao = _get_erp_field(serialized_response, 'erroExecucao')
    if erro_execucao:
        return {}, [f'ERP retornou erro: {_erp_value_to_text(erro_execucao)}']
    for record in _iter_erp_cliente_records(serialized_response):
        codigo = _erp_value_to_text(_get_erp_field(record, 'codcli', 'codCli'))
        nome = _erp_value_to_text(_get_erp_field(record, 'nomCli', 'nomcli'))
        if not codigo:
            errors.append('ERP retornou um cliente sem codigo.')
            continue
        if not codigo.isdigit():
            errors.append(f'ERP retornou codigo de cliente invalido: {codigo}.')
            continue
        if not nome:
            errors.append(f'ERP retornou o cliente {codigo} sem nome.')
            continue
        clientes[codigo] = nome

    if not clientes and not errors:
        errors.append('ERP nao retornou clientes para importar.')
    return clientes, errors


def _erp_wsdl_url(configuracao):
    base_url = (configuracao.url_erp if configuracao else ERP_CLIENTES_WSDL_URL).rstrip('/')
    if base_url.endswith('?wsdl'):
        return base_url
    return f'{base_url}{ERP_CLIENTES_SERVICE_PATH}?wsdl'


def _erp_public_base_url(configuracao):
    base_url = (configuracao.url_erp if configuracao else '').rstrip('/')
    if not base_url:
        return ERP_CLIENTES_PUBLIC_BASE_URL
    parsed = urlparse(base_url)
    netloc = parsed.hostname or parsed.netloc
    if parsed.username or parsed.password:
        netloc = parsed.netloc.rsplit('@', 1)[-1]
    if ':' not in netloc:
        netloc = f'{netloc}:8088'
    return urlunparse((parsed.scheme or 'http', netloc, '', '', '', ''))


class SeniorErpTransport(Transport):
    def __init__(self, *args, public_base_url=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.public_base_url = public_base_url or ERP_CLIENTES_PUBLIC_BASE_URL

    def _rewrite_url(self, url):
        return url.replace(ERP_CLIENTES_INTERNAL_BASE_URL, self.public_base_url)

    def load(self, url):
        return super().load(self._rewrite_url(url))

    def post(self, address, message, headers):
        return super().post(self._rewrite_url(address), message, headers)


def _buscar_clientes_erp():
    configuracao = ConfiguracaoSistema.objects.first()
    if not configuracao or not configuracao.usuario_erp or not configuracao.senha_erp:
        return {}, ['Configure URL, usuario e senha do ERP antes de importar clientes.']

    session = requests.Session()
    session.trust_env = False
    transport = SeniorErpTransport(
        session=session,
        timeout=ERP_CLIENTES_TIMEOUT,
        operation_timeout=ERP_CLIENTES_TIMEOUT,
        public_base_url=_erp_public_base_url(configuracao),
    )
    client = ZeepClient(_erp_wsdl_url(configuracao), transport=transport)
    response = client.service.buscarClientes(
        user=configuracao.usuario_erp,
        password=configuracao.senha_erp,
        encryption=configuracao.encryption_erp,
        parameters={},
    )
    return _extract_clientes_erp_data(response)


def _buscar_orcamentos_erp():
    configuracao = ConfiguracaoSistema.objects.first()
    if not configuracao or not configuracao.usuario_erp or not configuracao.senha_erp:
        return [], ['Configure URL, usuario e senha do ERP antes de importar orcamentos.']

    session = requests.Session()
    session.trust_env = False
    transport = SeniorErpTransport(
        session=session,
        timeout=ERP_CLIENTES_TIMEOUT,
        operation_timeout=ERP_CLIENTES_TIMEOUT,
        public_base_url=_erp_public_base_url(configuracao),
    )
    client = ZeepClient(_erp_wsdl_url(configuracao), transport=transport)
    response = client.service.buscarOrcamentos(
        user=configuracao.usuario_erp,
        password=configuracao.senha_erp,
        encryption=configuracao.encryption_erp,
        parameters={},
    )
    return _extract_orcamentos_erp_data(response)


def _clean_erp_orcamento_hours(value):
    text = _erp_value_to_text(value)
    if not text:
        return Decimal('0')
    try:
        return Decimal(text.replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError('horas invalidas') from exc


def _extract_orcamentos_erp_data(response):
    serialized_response = serialize_object(response)
    erro_execucao = _get_erp_field(serialized_response, 'erroExecucao')
    if erro_execucao:
        return [], [f'ERP retornou erro: {_erp_value_to_text(erro_execucao)}']

    orcamentos = []
    errors = []
    for record in _iter_erp_orcamento_records(serialized_response):
        codigo = _erp_value_to_text(_get_erp_field(record, 'numOrc', 'numorc'))
        nome = _erp_value_to_text(_get_erp_field(record, 'nomOrc', 'nomorc'))
        codigo_cliente = _erp_value_to_text(_get_erp_field(record, 'codCli', 'codcli'))
        numero_chamado = _erp_value_to_text(_get_erp_field(record, 'numCha', 'numcha'))
        codigo_responsavel = _erp_value_to_text(_get_erp_field(record, 'codRep', 'codrep'))
        try:
            horas = _clean_erp_orcamento_hours(_get_erp_field(record, 'qtdHrs', 'qtdhrs'))
            horas_apontadas = _clean_erp_orcamento_hours(_get_erp_field(record, 'qtdCon', 'qtdcon'))
        except ValueError:
            orcamentos.append(
                {
                    'codigo': codigo,
                    'nome': nome,
                    'codigo_cliente': codigo_cliente,
                    'numero_chamado': numero_chamado,
                    'codigo_responsavel': codigo_responsavel,
                    'horas': None,
                    'horas_apontadas': None,
                    'erro': 'Quantidade de horas invalida.',
                }
            )
            continue
        orcamentos.append(
            {
                'codigo': codigo,
                'nome': nome,
                'codigo_cliente': codigo_cliente,
                'numero_chamado': numero_chamado,
                'codigo_responsavel': codigo_responsavel,
                'horas': horas,
                'horas_apontadas': horas_apontadas,
            }
        )

    if not orcamentos and not errors:
        errors.append('ERP nao retornou orcamentos para importar.')
    return orcamentos, errors


def _extract_servicos_erp_data(response):
    servicos = {}
    errors = []
    serialized_response = serialize_object(response)
    erro_execucao = _get_erp_field(serialized_response, 'erroExecucao')
    if erro_execucao:
        return {}, [f'ERP retornou erro: {_erp_value_to_text(erro_execucao)}']

    for record in _iter_erp_servico_records(serialized_response):
        codigo = _erp_value_to_text(_get_erp_field(record, 'codSer', 'codser'))
        descricao = _erp_value_to_text(_get_erp_field(record, 'desSer', 'desser'))
        if not codigo:
            errors.append('ERP retornou um servico sem codigo.')
            continue
        if not descricao:
            errors.append(f'ERP retornou o servico {codigo} sem descricao.')
            continue
        servicos[codigo] = descricao

    if not servicos and not errors:
        errors.append('ERP nao retornou servicos para importar.')
    return servicos, errors


def _salvar_clientes_data(clientes_data, user):
    created_count = 0
    updated_count = 0
    with transaction.atomic():
        for codigo, nome in clientes_data.items():
            _, created = Cliente.objects.update_or_create(
                Codigo_Cliente=codigo,
                defaults={
                    'Nome_Cliente': nome,
                    'Situacao': Cliente.SITUACAO_ATIVO,
                    'Usuario_Alteracao': user,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
    return created_count, updated_count


def _buscar_servicos_erp():
    configuracao = ConfiguracaoSistema.objects.first()
    if not configuracao or not configuracao.usuario_erp or not configuracao.senha_erp:
        return {}, ['Configure URL, usuario e senha do ERP antes de importar servicos.']

    session = requests.Session()
    session.trust_env = False
    transport = SeniorErpTransport(
        session=session,
        timeout=ERP_CLIENTES_TIMEOUT,
        operation_timeout=ERP_CLIENTES_TIMEOUT,
        public_base_url=_erp_public_base_url(configuracao),
    )
    client = ZeepClient(_erp_wsdl_url(configuracao), transport=transport)
    response = client.service.buscarServicos(
        user=configuracao.usuario_erp,
        password=configuracao.senha_erp,
        encryption=configuracao.encryption_erp,
        parameters={},
    )
    return _extract_servicos_erp_data(response)


def _buscar_servicos_orcamentos_erp():
    configuracao = ConfiguracaoSistema.objects.first()
    if not configuracao or not configuracao.usuario_erp or not configuracao.senha_erp:
        return [], ['Configure URL, usuario e senha do ERP antes de importar ligacoes de servicos.']

    session = requests.Session()
    session.trust_env = False
    transport = SeniorErpTransport(
        session=session,
        timeout=ERP_CLIENTES_TIMEOUT,
        operation_timeout=ERP_CLIENTES_TIMEOUT,
        public_base_url=_erp_public_base_url(configuracao),
    )
    client = ZeepClient(_erp_wsdl_url(configuracao), transport=transport)
    response = client.service.ligacaoServicoOrcamento(
        user=configuracao.usuario_erp,
        password=configuracao.senha_erp,
        encryption=configuracao.encryption_erp,
        parameters={},
    )
    return _extract_servicos_orcamentos_erp_data(response)


def _salvar_servicos_data(servicos_data):
    created_count = 0
    updated_count = 0
    with transaction.atomic():
        for codigo, descricao in servicos_data.items():
            _, created = Servico.objects.update_or_create(
                codigo=codigo,
                defaults={'descricao': descricao},
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
    return created_count, updated_count


def _extract_servicos_orcamentos_erp_data(response):
    ligacoes = []
    errors = []
    serialized_response = serialize_object(response)
    erro_execucao = _get_erp_field(serialized_response, 'erroExecucao')
    if erro_execucao:
        return [], [f'ERP retornou erro: {_erp_value_to_text(erro_execucao)}']

    for record in _iter_erp_servico_orcamento_records(serialized_response):
        ligacoes.append(
            {
                'codigo_orcamento': _erp_value_to_text(_get_erp_field(record, 'numOrc', 'numorc')),
                'codigo_servico': _erp_value_to_text(_get_erp_field(record, 'codSer', 'codser')),
            }
        )

    if not ligacoes and not errors:
        errors.append('ERP nao retornou ligacoes de servicos para importar.')
    return ligacoes, errors


def _salvar_servicos_orcamentos_erp(ligacoes_data):
    created_count = 0
    updated_count = 0
    rejected = []
    orcamentos_por_codigo = {orcamento.codigo: orcamento for orcamento in Orcamento.objects.all()}
    servicos_por_codigo = {servico.codigo: servico for servico in Servico.objects.all()}

    with transaction.atomic():
        for item in ligacoes_data:
            codigo_orcamento = item['codigo_orcamento']
            codigo_servico = item['codigo_servico']
            motivos = []
            if not codigo_orcamento:
                motivos.append('Numero do orcamento nao informado.')
            if not codigo_servico:
                motivos.append('Codigo do servico nao informado.')

            orcamento = orcamentos_por_codigo.get(codigo_orcamento)
            servico = servicos_por_codigo.get(codigo_servico)
            if codigo_orcamento and not orcamento:
                motivos.append(f'Orcamento {codigo_orcamento} nao cadastrado.')
            if codigo_servico and not servico:
                motivos.append(f'Servico {codigo_servico} nao cadastrado.')

            if motivos:
                rejected.append(
                    {
                        'codigo': f'{codigo_orcamento or "-"} / {codigo_servico or "-"}',
                        'motivo': ' '.join(motivos),
                    }
                )
                continue

            _, created = OrcamentoServico.objects.get_or_create(orcamento=orcamento, servico=servico)
            if created:
                created_count += 1
            else:
                updated_count += 1

    return created_count, updated_count, rejected


def _salvar_orcamentos_erp(orcamentos_data):
    created_count = 0
    updated_count = 0
    rejected = []

    cliente_por_codigo = {
        cliente.Codigo_Cliente: cliente
        for cliente in Cliente.objects.all()
    }
    usuarios_por_codigo_erp = {
        str(profile.codigoerp): profile.user
        for profile in UserProfile.objects.select_related('user').exclude(codigoerp=0)
    }

    with transaction.atomic():
        for item in orcamentos_data:
            codigo = item['codigo']
            motivos = []
            if item.get('erro'):
                motivos.append(item['erro'])
            if not codigo:
                motivos.append('Codigo do orcamento nao informado.')
            if codigo and not codigo.isdigit():
                motivos.append('Codigo do orcamento deve conter somente numeros.')
            if item['codigo_cliente'] and not item['codigo_cliente'].isdigit():
                motivos.append('Codigo do cliente deve conter somente numeros.')
            if item['numero_chamado'] and not item['numero_chamado'].isdigit():
                motivos.append('Numero do chamado deve conter somente numeros.')
            if item['codigo_responsavel'] and not item['codigo_responsavel'].isdigit():
                motivos.append('Codigo ERP do responsavel deve conter somente numeros.')

            cliente = cliente_por_codigo.get(item['codigo_cliente'])
            if not item['codigo_cliente']:
                motivos.append('Codigo do cliente nao informado.')
            elif not cliente:
                motivos.append(f'Cliente {item["codigo_cliente"]} nao cadastrado.')

            responsavel = usuarios_por_codigo_erp.get(item['codigo_responsavel'])
            if not item['codigo_responsavel']:
                motivos.append('Codigo ERP do responsavel nao informado.')
            elif not responsavel:
                motivos.append(f'Usuario com codigo ERP {item["codigo_responsavel"]} nao cadastrado.')

            if motivos:
                rejected.append({'codigo': codigo or '-', 'motivo': ' '.join(motivos)})
                continue

            _, created = Orcamento.objects.update_or_create(
                codigo=codigo,
                defaults={
                    'codigo_cliente': cliente.Codigo_Cliente,
                    'nome_cliente': cliente.Nome_Cliente,
                    'numero_chamado': item['numero_chamado'],
                    'nome': item['nome'],
                    'horas': item['horas'],
                    'horas_apontadas': item['horas_apontadas'],
                    'responsavel': responsavel,
                    'pmo': responsavel,
                    'ativo': True,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

    return created_count, updated_count, rejected

def _validate_orcamentos_import(rows):
    errors = []
    if not rows:
        return [], ['A planilha estÃ¡ vazia.']

    headers = [_normalize_estimativa_text(value) for value in rows[0]]
    if headers != ORCAMENTO_IMPORT_HEADERS:
        return [], [
            'Os cabeÃ§alhos devem estar nesta ordem: orcamento, cliente, chamado, descricao, qtd_horas, pmo.'
        ]

    existing_codes = set(Orcamento.objects.values_list('codigo', flat=True))
    imported_codes = set()
    orcamentos = []
    horas_field = DurationField(required=True, compact_digits=True)
    for row_number, row in enumerate(rows[1:], start=2):
        values = (row + ['', '', '', '', '', ''])[:6]
        if not any(values):
            continue
        codigo, codigo_cliente, numero_chamado, nome, qtd_horas, pmo_text = (
            value.strip() for value in values
        )
        if not codigo:
            errors.append(f'Linha {row_number}: CÃ³digo OrÃ§amento Ã© obrigatÃ³rio.')
            continue
        horas = None
        pmo_user = None
        for label, value in (
            ('CÃ³digo OrÃ§amento', codigo),
            ('CÃ³digo Cliente', codigo_cliente),
            ('NÃºmero do Chamado', numero_chamado),
        ):
            if value and not value.isdigit():
                errors.append(f'Linha {row_number}: {label} deve conter somente nÃºmeros.')
        if not qtd_horas:
            errors.append(f'Linha {row_number}: Quantidade de Horas e obrigatoria.')
        else:
            try:
                horas = _clean_orcamento_import_hours(qtd_horas, horas_field)
            except ValidationError:
                errors.append(f'Linha {row_number}: Quantidade de Horas deve estar no formato HH:MM.')
            else:
                if horas <= 0:
                    errors.append(f'Linha {row_number}: Quantidade de Horas deve ser maior que zero.')
        if not pmo_text:
            errors.append(f'Linha {row_number}: PMO e obrigatorio.')
        else:
            pmo_user = _find_pmo_user(pmo_text)
            if pmo_user is None:
                errors.append(f'Linha {row_number}: PMO "{pmo_text}" nao existe ou nao esta marcado como PMO.')
        if codigo in existing_codes:
            errors.append(f'Linha {row_number}: o orÃ§amento {codigo} jÃ¡ existe na base.')
        if codigo in imported_codes:
            errors.append(f'Linha {row_number}: o orÃ§amento {codigo} estÃ¡ duplicado na planilha.')
        imported_codes.add(codigo)
        orcamentos.append(
            Orcamento(
                codigo=codigo,
                codigo_cliente=codigo_cliente,
                numero_chamado=numero_chamado,
                nome=nome,
                horas=horas or Decimal('0'),
                pmo=pmo_user,
            )
        )
    if not orcamentos and not errors:
        errors.append('A planilha nÃ£o possui orÃ§amentos para importar.')
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
        context['is_admin'] = _user_is_admin(self.request.user)
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
        folgas_feriados = list(_agenda_folga_feriado_queryset(self.request, selected_users, month_start))
        for atividade in atividades:
            atividade.can_manage = _can_manage_agenda_activity(self.request.user, atividade)
        for folga in folgas_feriados:
            folga.can_manage = folga.criado_por_id == self.request.user.pk

        prev_month, next_month = _month_navigation(month_start)
        context['section'] = 'agenda'
        context['is_gp'] = _user_is_gp(self.request.user)
        context['agenda_compare_mode'] = len(selected_users) > 1
        context['agenda_weeks'] = (
            _build_agenda_calendar(month_start, atividades, selected_users, folgas_feriados) if selected_users else []
        )
        context['agenda_atividades'] = atividades
        context['agenda_folgas_feriados'] = folgas_feriados
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


class FolgasFeriadosView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/folgas_feriados.html'

    def _folgas_queryset(self):
        queryset = _base_folga_feriado_queryset()
        if _user_is_gp(self.request.user):
            return queryset.order_by('-data', '-abrangencia_todos', 'user__username', 'descricao')
        return queryset.filter(
            Q(user=self.request.user) | Q(criado_por=self.request.user) | Q(abrangencia_todos=True)
        ).order_by('-data', '-abrangencia_todos', 'descricao')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folga = getattr(self, 'folga', None)
        form = kwargs.get('form') or FolgaFeriadoForm(instance=folga, current_user=self.request.user)
        context['section'] = 'folgas_feriados'
        context['is_gp'] = _user_is_gp(self.request.user)
        context['form'] = form
        context['folga'] = folga
        context['folgas_feriados'] = self._folgas_queryset()
        return context

    def _save_form(self, form):
        folga = form.save(commit=False)
        if form.cleaned_data.get('aplicar_todos') and _user_is_gp(self.request.user):
            folga.user = None
            folga.abrangencia_todos = True
        elif not _user_is_gp(self.request.user):
            folga.user = self.request.user
            folga.abrangencia_todos = False
        elif not folga.abrangencia_todos:
            folga.abrangencia_todos = False

        if not folga.criado_por_id:
            folga.criado_por = self.request.user
        folga.save()
        return 1

    def post(self, request, *args, **kwargs):
        form = FolgaFeriadoForm(request.POST, current_user=request.user)
        if form.is_valid():
            total = self._save_form(form)
            messages.success(request, f'{total} folga/feriado(s) salvo(s) com sucesso.')
            return redirect('horas:folgas_feriados')

        messages.error(request, 'Corrija os campos destacados antes de salvar a folga/feriado.')
        return self.render_to_response(self.get_context_data(form=form))


class FolgaFeriadoUpdateView(FolgasFeriadosView):
    def dispatch(self, request, *args, **kwargs):
        self.folga = get_object_or_404(_folga_feriado_manage_queryset(request.user), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = FolgaFeriadoForm(request.POST, instance=self.folga, current_user=request.user)
        if form.is_valid():
            folga = form.save(commit=False)
            folga.criado_por = self.folga.criado_por
            if self.folga.abrangencia_todos:
                folga.user = None
                folga.abrangencia_todos = True
            elif not _user_is_gp(request.user):
                folga.user = request.user
                folga.abrangencia_todos = False
            else:
                folga.abrangencia_todos = False
            folga.save()
            messages.success(request, 'Folga/feriado atualizado com sucesso.')
            return redirect('horas:folgas_feriados')

        messages.error(request, 'Corrija os campos destacados antes de salvar a folga/feriado.')
        return self.render_to_response(self.get_context_data(form=form))

@method_decorator(login_required(login_url='login'), name='dispatch')
class FolgaFeriadoDeleteView(View):
    def post(self, request, pk):
        folga = get_object_or_404(_base_folga_feriado_queryset(), pk=pk)
        if folga.criado_por_id != request.user.pk:
            messages.error(request, 'Só é possível excluir registros criados pelo próprio usuário.')
            return redirect('horas:folgas_feriados')

        folga.delete()
        messages.success(request, 'Folga/feriado removido com sucesso.')
        return redirect('horas:folgas_feriados')

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
            servico_id = self.request.GET.get('servico')
            if data_inicial:
                initial['data'] = data_inicial
            if orcamento_id and Orcamento.objects.filter(pk=orcamento_id).exists():
                initial['orcamento'] = orcamento_id
            if servico_id and Servico.objects.filter(pk=servico_id).exists():
                initial['servico'] = servico_id
            form = RegistroForm(initial=initial)
        context['form'] = form
        context['extra_rows'] = kwargs.get('extra_rows') or []
        context['descricao_max_length'] = REGISTRO_DESCRICAO_MAX_LENGTH
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
            'servico': request.POST.get('servico', ''),
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
        registros, data_inicial, data_final, orcamento_id, usuario_id = _filter_registros(
            self.request,
            allow_usuario_filter=True,
        )
        can_filter_usuario = _user_can_export_csv(self.request.user)
        context['section'] = 'registros'
        context['registros'] = registros
        context['usuarios_filtro'] = User.objects.order_by('username') if can_filter_usuario else []
        context['can_filter_usuario'] = can_filter_usuario
        context['filtros'] = {
            'de': data_inicial.isoformat() if data_inicial else '',
            'ate': data_final.isoformat() if data_final else '',
            'orcamento': orcamento_id or '',
            'usuario': usuario_id or '',
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
        if self.registro.processado == Registro.PROCESSADO_SIM:
            raise PermissionDenied
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
        if registro.processado == Registro.PROCESSADO_SIM:
            raise PermissionDenied
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
        if not _user_can_export_csv(request.user):
            raise PermissionDenied
        registro = get_object_or_404(_base_registros_queryset(request.user, include_all_users=True), pk=pk)
        if registro.processado == Registro.PROCESSADO_SIM:
            raise PermissionDenied
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
        registros, data_inicial, data_final, _, _ = _filter_registros(self.request)
        registros_list = list(registros)
        total_horas = sum(registro.total_horas for registro in registros_list)
        dias_trabalhados = len({registro.data for registro in registros_list})
        media_diaria = total_horas / dias_trabalhados if dias_trabalhados else 0

        por_orcamento = defaultdict(lambda: {'count': 0, 'hours': 0, 'codigo': 'â€”', 'nome': ''})
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
                f'SolicitaÃ§Ã£o {solicitacao.numero_solicitacao} enviada para aprovaÃ§Ã£o.',
            )
            return redirect('horas:solicitacoes_horas')

        messages.error(request, 'Corrija os campos destacados antes de enviar a solicitaÃ§Ã£o.')
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
        observacao = request.POST.get(
            'observacao',
            request.POST.get('motivo_reprovacao', ''),
        ).strip()

        with transaction.atomic():
            solicitacao = get_object_or_404(
                SolicitacaoHoras.objects.select_for_update().select_related('orcamento'),
                pk=pk,
                orcamento__responsavel=request.user,
            )
            if solicitacao.situacao != SolicitacaoHoras.SITUACAO_AGUARDANDO:
                messages.warning(request, 'Esta solicitaÃ§Ã£o jÃ¡ foi processada.')
                return redirect('horas:solicitacoes_horas_pendentes')

            if decisao == 'aprovar':
                solicitacao.situacao = SolicitacaoHoras.SITUACAO_APROVADO
                solicitacao.motivo_reprovacao = observacao
                mensagem = 'SolicitaÃ§Ã£o aprovada.'
            elif decisao == 'reprovar':
                if not observacao:
                    messages.error(request, 'Informe a observaÃ§Ã£o para reprovar a solicitaÃ§Ã£o.')
                    return redirect('horas:solicitacoes_horas_pendentes')
                solicitacao.situacao = SolicitacaoHoras.SITUACAO_REPROVADO
                solicitacao.motivo_reprovacao = observacao
                mensagem = 'SolicitaÃ§Ã£o reprovada.'
            else:
                messages.error(request, 'DecisÃ£o invÃ¡lida.')
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


class ConfiguracoesView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/configuracoes.html'

    def get_configuracao(self):
        configuracao = ConfiguracaoSistema.objects.first()
        if configuracao:
            return configuracao
        return ConfiguracaoSistema(url_erp='http://wsadmteste.magnus.com.br', encryption_erp=0)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        configuracao = kwargs.get('configuracao') or self.get_configuracao()
        context['section'] = 'configuracoes'
        context['configuracao'] = configuracao if configuracao.pk else None
        context['form'] = kwargs.get('form') or ConfiguracaoSistemaForm(instance=configuracao)
        return context

    def post(self, request, *args, **kwargs):
        configuracao = ConfiguracaoSistema.objects.first()
        form = ConfiguracaoSistemaForm(request.POST, instance=configuracao)
        if form.is_valid():
            configuracao = form.save(commit=False)
            configuracao.encryption_erp = 0
            configuracao.save()
            messages.success(request, 'Configuracoes salvas com sucesso.')
            return redirect('horas:configuracoes')

        messages.error(request, 'Corrija os campos destacados antes de salvar as configuracoes.')
        return self.render_to_response(self.get_context_data(form=form, configuracao=configuracao))



class ClientesView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/clientes.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtro_codigo = self.request.GET.get('codigo', '').strip()
        filtro_nome = self.request.GET.get('nome', '').strip()
        clientes = Cliente.objects.select_related('Usuario_Alteracao').order_by('Codigo_Cliente')
        if filtro_codigo:
            clientes = clientes.filter(Codigo_Cliente__icontains=filtro_codigo)
        if filtro_nome:
            clientes = clientes.filter(Nome_Cliente__icontains=filtro_nome)
        context['section'] = 'clientes'
        context['form'] = kwargs.get('form') or ClienteForm()
        context['import_form'] = kwargs.get('import_form') or ClienteImportForm()
        context['import_errors'] = kwargs.get('import_errors') or []
        context['clientes'] = clientes
        context['filtros_clientes'] = {
            'codigo': filtro_codigo,
            'nome': filtro_nome,
        }
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('action') == 'importar_erp':
            try:
                clientes_data, import_errors = _buscar_clientes_erp()
            except Exception as exc:
                logger.exception('Erro ao consultar clientes no ERP: %s', exc)
                messages.error(request, 'Nao foi possivel consultar os clientes no ERP. Tente novamente mais tarde.')
                return redirect('horas:clientes')

            if import_errors:
                messages.error(request, 'ERP retornou dados invalidos e nenhum cliente foi importado.')
                return self.render_to_response(self.get_context_data(import_errors=import_errors))

            created_count, updated_count = _salvar_clientes_data(clientes_data, request.user)
            messages.success(
                request,
                (
                    'Importacao do ERP concluida: '
                    f'{created_count} cliente(s) criado(s) e {updated_count} atualizado(s) com sucesso.'
                ),
            )
            return redirect('horas:clientes')

        if request.POST.get('action') == 'importar':
            import_form = ClienteImportForm(request.POST, request.FILES)
            if import_form.is_valid():
                try:
                    rows = _read_orcamentos_xlsx(import_form.cleaned_data['arquivo'])
                    clientes_data, import_errors = _validate_clientes_import(rows)
                except ValueError as exc:
                    import_errors = [str(exc)]
                    clientes_data = {}
                if not import_errors:
                    created_count, updated_count = _salvar_clientes_data(clientes_data, request.user)
                    messages.success(
                        request,
                        f'{created_count} cliente(s) criado(s) e {updated_count} atualizado(s) com sucesso.',
                    )
                    return redirect('horas:clientes')
                messages.error(request, 'A planilha possui erros e nenhum cliente foi importado.')
                return self.render_to_response(
                    self.get_context_data(import_form=import_form, import_errors=import_errors)
                )
            messages.error(request, 'Selecione uma planilha XLSX valida.')
            return self.render_to_response(self.get_context_data(import_form=import_form))

        form = ClienteForm(request.POST)
        if form.is_valid():
            form.save(user=request.user)
            messages.success(request, 'Cliente adicionado com sucesso.')
            return redirect('horas:clientes')

        messages.error(request, 'Corrija os campos destacados antes de adicionar o cliente.')
        return self.render_to_response(self.get_context_data(form=form))



class ClienteUpdateView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/cliente_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.cliente = get_object_or_404(Cliente, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'clientes'
        context['cliente'] = self.cliente
        context['form'] = kwargs.get('form') or ClienteEditForm(instance=self.cliente)
        return context

    def post(self, request, *args, **kwargs):
        form = ClienteEditForm(request.POST, instance=self.cliente)
        if form.is_valid():
            form.save(user=request.user)
            messages.success(request, 'Cliente atualizado com sucesso.')
            return redirect('horas:clientes')

        messages.error(request, 'Corrija os campos destacados antes de salvar o cliente.')
        return self.render_to_response(self.get_context_data(form=form))


class OrcamentosView(
    GerenteProjetosRequiredMixin,
    AuthenticatedViewMixin,
    SidebarContextMixin,
    TemplateView,
):
    template_name = 'horas/orcamentos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtro_codigo = self.request.GET.get('codigo', '').strip()
        filtro_descricao = self.request.GET.get('descricao', '').strip()
        orcamentos = Orcamento.objects.select_related('responsavel', 'pmo').order_by('codigo')
        if filtro_codigo:
            orcamentos = orcamentos.filter(codigo__icontains=filtro_codigo)
        if filtro_descricao:
            orcamentos = orcamentos.filter(nome__icontains=filtro_descricao)
        context['section'] = 'orcamentos'
        context['form'] = kwargs.get('form') or OrcamentoForm()
        context['import_form'] = kwargs.get('import_form') or OrcamentoImportForm()
        context['import_errors'] = kwargs.get('import_errors') or []
        context['erp_import_rejections'] = kwargs.get('erp_import_rejections') or []
        context['orcamentos'] = orcamentos
        context['filtros_orcamentos'] = {
            'codigo': filtro_codigo,
            'descricao': filtro_descricao,
        }
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('action') == 'importar_erp':
            try:
                orcamentos_data, import_errors = _buscar_orcamentos_erp()
            except Exception as exc:
                logger.exception('Erro ao consultar orcamentos no ERP: %s', exc)
                messages.error(request, 'Nao foi possivel consultar os orcamentos no ERP. Tente novamente mais tarde.')
                return redirect('horas:orcamentos')

            if import_errors:
                messages.error(request, 'ERP retornou dados invalidos e nenhum orcamento foi importado.')
                return self.render_to_response(self.get_context_data(erp_import_rejections=[
                    {'codigo': '-', 'motivo': error} for error in import_errors
                ]))

            created_count, updated_count, rejected = _salvar_orcamentos_erp(orcamentos_data)
            messages.success(
                request,
                (
                    'Importacao do ERP concluida: '
                    f'{created_count} orcamento(s) criado(s) e {updated_count} atualizado(s) com sucesso.'
                ),
            )
            if rejected:
                messages.warning(
                    request,
                    f'{len(rejected)} orcamento(s) nao foram importado(s). Consulte os motivos na tela.',
                )
                return self.render_to_response(self.get_context_data(erp_import_rejections=rejected))
            return redirect('horas:orcamentos')

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
        queryset = Orcamento.objects.select_related('responsavel', 'pmo')
        if not _user_is_admin(request.user):
            queryset = queryset.filter(responsavel=request.user)
        self.orcamento = get_object_or_404(queryset, pk=kwargs['pk'])
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


class ServicosView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/servicos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'servicos'
        context['form'] = kwargs.get('form') or ServicoForm()
        context['import_errors'] = kwargs.get('import_errors') or []
        context['servicos'] = Servico.objects.order_by('codigo')
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('action') == 'importar_erp':
            try:
                servicos_data, import_errors = _buscar_servicos_erp()
            except Exception as exc:
                logger.exception('Erro ao consultar servicos no ERP: %s', exc)
                messages.error(request, 'Nao foi possivel consultar os servicos no ERP. Tente novamente mais tarde.')
                return redirect('horas:servicos')

            if import_errors:
                messages.error(request, 'ERP retornou dados invalidos e nenhum servico foi importado.')
                return self.render_to_response(self.get_context_data(import_errors=import_errors))

            created_count, updated_count = _salvar_servicos_data(servicos_data)
            messages.success(
                request,
                (
                    'Importacao do ERP concluida: '
                    f'{created_count} servico(s) criado(s) e {updated_count} atualizado(s) com sucesso.'
                ),
            )
            return redirect('horas:servicos')

        form = ServicoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'ServiÃ§o adicionado com sucesso.')
            return redirect('horas:servicos')

        messages.error(request, 'Corrija os campos destacados antes de adicionar o serviÃ§o.')
        return self.render_to_response(self.get_context_data(form=form))


class ServicoOrcamentoView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/servico_orcamento.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        orcamento_id = (kwargs.get('selected_orcamento_id') or self.request.GET.get('orcamento', '')).strip()
        orcamentos = Orcamento.objects.filter(ativo=True).order_by('codigo')
        selected_orcamento = None
        servicos_vinculados = OrcamentoServico.objects.none()
        if orcamento_id:
            selected_orcamento = Orcamento.objects.filter(pk=orcamento_id).first()
            if selected_orcamento:
                servicos_vinculados = selected_orcamento.servicos_vinculados.select_related('servico').order_by(
                    'servico__codigo'
                )
        context['section'] = 'servico_orcamento'
        context['orcamentos'] = orcamentos
        context['selected_orcamento'] = selected_orcamento
        context['selected_orcamento_id'] = orcamento_id
        context['servicos_vinculados'] = servicos_vinculados
        context['erp_import_rejections'] = kwargs.get('erp_import_rejections') or []
        return context

    def post(self, request, *args, **kwargs):
        orcamento_id = request.POST.get('orcamento', '').strip()
        try:
            ligacoes_data, import_errors = _buscar_servicos_orcamentos_erp()
        except Exception as exc:
            logger.exception('Erro ao consultar ligacoes de servico x orcamento no ERP: %s', exc)
            messages.error(request, 'Nao foi possivel consultar as ligacoes no ERP. Tente novamente mais tarde.')
            return redirect('horas:servico_orcamento')

        if import_errors:
            messages.error(request, 'ERP retornou dados invalidos e nenhuma ligacao foi importada.')
            return self.render_to_response(
                self.get_context_data(
                    selected_orcamento_id=orcamento_id,
                    erp_import_rejections=[{'codigo': '-', 'motivo': error} for error in import_errors]
                )
            )

        created_count, updated_count, rejected = _salvar_servicos_orcamentos_erp(ligacoes_data)
        messages.success(
            request,
            (
                'Importacao do ERP concluida: '
                f'{created_count} ligacao(oes) criada(s) e {updated_count} atualizada(s) com sucesso.'
            ),
        )
        if rejected:
            messages.warning(
                request,
                f'{len(rejected)} ligacao(oes) nao foram atualizada(s). Consulte os motivos na tela.',
            )
            return self.render_to_response(
                self.get_context_data(selected_orcamento_id=orcamento_id, erp_import_rejections=rejected)
            )

        url = reverse('horas:servico_orcamento')
        if orcamento_id:
            url = f'{url}?{urlencode({"orcamento": orcamento_id})}'
        return redirect(url)


class OrcamentoDeleteView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, View):
    def post(self, request, pk):
        queryset = Orcamento.objects.all()
        if not _user_is_admin(request.user):
            queryset = queryset.filter(responsavel=request.user)
        orcamento = get_object_or_404(queryset, pk=pk)
        try:
            orcamento.delete()
            messages.success(request, 'OrÃ§amento removido.')
        except ProtectedError:
            orcamento.ativo = False
            orcamento.save(update_fields=['ativo'])
            messages.warning(
                request,
                'O orÃ§amento possui registros vinculados e foi desativado em vez de removido.',
            )
        return redirect('horas:orcamentos')


class FaseDeleteView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, View):
    def post(self, request, pk):
        fase = get_object_or_404(Fase, pk=pk)
        fase.delete()
        messages.success(request, 'Fase removida.')
        return redirect('horas:fases')


class ServicoDeleteView(GerenteProjetosRequiredMixin, AuthenticatedViewMixin, View):
    def post(self, request, pk):
        servico = get_object_or_404(Servico, pk=pk)
        servico.delete()
        messages.success(request, 'ServiÃ§o removido.')
        return redirect('horas:servicos')


@login_required(login_url='login')
def exportar_registros_csv(request):
    if not _user_can_export_csv(request.user):
        raise PermissionDenied

    registros, _, _, _, _ = _filter_registros(request, allow_usuario_filter=True)
    with transaction.atomic():
        registros_exportados = list(
            registros.select_for_update(of=('self',)).filter(processado=Registro.PROCESSADO_NAO)
        )
        if not registros_exportados:
            messages.warning(request, 'Nenhum registro pendente para exportar.')
            return redirect('horas:registros')

        Registro.objects.filter(
            pk__in=[registro.pk for registro in registros_exportados],
        ).update(processado=Registro.PROCESSADO_SIM, atualizado_em=timezone.now())

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="apontamento_{date.today().isoformat()}.csv"'
    )
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Consultor',
        'Orcamento',
        'Servico',
        'Data',
        'Hora Inicio',
        'Hora Fim',
        'Descr Atividade',
        'Nr Chamado',
        'Cod. Fase',
    ])
    for registro in registros_exportados:
        writer.writerow(
            [
                getattr(getattr(registro.user, 'profile', None), 'codigoerp', 0),
                registro.orcamento.codigo,
                registro.servico.codigo if registro.servico else '',
                registro.data.strftime('%d/%m/%Y'),
                registro.hora_inicio.strftime('%H:%M'),
                registro.hora_fim.strftime('%H:%M'),
                registro.descricao,
                registro.orcamento.numero_chamado,
                registro.fase.codigo if registro.fase else '',
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
