import csv
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .forms import (
    EstimativaForm,
    EstimativaItemCreateFormSet,
    EstimativaItemFormSet,
    FaseForm,
    OrcamentoForm,
    RegistroForm,
)
from .models import Estimativa, Fase, Orcamento, Registro


XLSX_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('', XLSX_NS)
ET.register_namespace('r', REL_NS)


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


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _base_registros_queryset(user):
    return Registro.objects.select_related('orcamento', 'fase').filter(user=user)


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
        return context


class AuthenticatedViewMixin(LoginRequiredMixin):
    login_url = 'login'


class DashboardView(AuthenticatedViewMixin, RedirectView):
    pattern_name = 'horas:timer'


class TimerView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/timer.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'timer'
        context['form'] = kwargs.get('form') or RegistroForm()
        context['extra_rows'] = kwargs.get('extra_rows') or []
        return context

    def post(self, request, *args, **kwargs):
        submission_mode = request.POST.get('submission_mode', 'manual')
        if submission_mode == 'timer':
            form = RegistroForm(request.POST)
            if form.is_valid():
                registro = form.save(commit=False)
                registro.user = request.user
                registro.save()
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
            for form in row_forms:
                registro = form.save(commit=False)
                registro.user = request.user
                registro.save()
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
            registro.save()
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


class OrcamentosView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
    template_name = 'horas/orcamentos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'orcamentos'
        context['form'] = kwargs.get('form') or OrcamentoForm()
        context['orcamentos'] = Orcamento.objects.order_by('codigo')
        return context

    def post(self, request, *args, **kwargs):
        form = OrcamentoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Orçamento adicionado com sucesso.')
            return redirect('horas:orcamentos')

        messages.error(request, 'Corrija os campos destacados antes de adicionar o orçamento.')
        return self.render_to_response(self.get_context_data(form=form))


class FasesView(AuthenticatedViewMixin, SidebarContextMixin, TemplateView):
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


@method_decorator(login_required(login_url='login'), name='dispatch')
class OrcamentoDeleteView(View):
    def post(self, request, pk):
        orcamento = get_object_or_404(Orcamento, pk=pk)
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


@method_decorator(login_required(login_url='login'), name='dispatch')
class FaseDeleteView(View):
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
