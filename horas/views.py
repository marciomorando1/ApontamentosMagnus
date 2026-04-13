import csv
from collections import defaultdict
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .forms import FaseForm, OrcamentoForm, RegistroForm
from .models import Fase, Orcamento, Registro


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


def _format_decimal_hours(value):
    total_minutes = int(round((value or 0) * 60))
    hours, minutes = divmod(total_minutes, 60)
    return f'{hours}h{minutes:02d}'


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
