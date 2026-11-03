from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import SolicitacaoForm
from .models import AgendaAtividade, Cliente, Orcamento, Solicitacao


class AbrirSolicitacaoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='solicitante')
        self.pmo = get_user_model().objects.create_user(username='pmo')
        self.pmo.profile.is_pmo = True
        self.pmo.profile.save()
        self.budget = Orcamento.objects.create(codigo='100', pmo=self.pmo,
            codigo_cliente='10', nome_cliente='Cliente base', numero_chamado='123')
        self.data = dict(orcamento='Livre', tipo='ESTIMATIVA', descricao='Nova demanda',
            pmo='PMO livre', produto='ERP', codigo_cliente='20', cliente='Novo cliente',
            descritivo_necessidade='Detalhes da demanda', quantidade_horas='01:30',
            data_maxima_entrega='2026-12-01')

    def test_login_required(self):
        self.assertEqual(self.client.get(reverse('horas:abrir_solicitacao')).status_code, 302)

    def test_hours_use_hhmm_and_preserve_existing_decimal_values(self):
        for value, expected in [('01:30', '1.50'), ('00:01', '0.02'), ('125:45', '125.75')]:
            form = SolicitacaoForm({**self.data, 'quantidade_horas': value,
                                    'quantidade_horas_executadas': '00:00'})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['quantidade_horas'], Decimal(expected))
            self.assertEqual(form.cleaned_data['quantidade_horas_executadas'], Decimal('0'))
            self.assertEqual(form.instance.quantidade_horas_formatadas, value)
        item = Solicitacao(quantidade_horas=Decimal('1.50'), quantidade_horas_executadas=Decimal('2.25'))
        form = SolicitacaoForm(instance=item)
        self.assertEqual(form['quantidade_horas'].value(), '01:30')
        self.assertEqual(form['quantidade_horas_executadas'].value(), '02:15')
        self.assertEqual(item.quantidade_horas_executadas_formatadas, '02:15')

    def test_hours_reject_invalid_minutes_and_decimal_input(self):
        for name in ('quantidade_horas', 'quantidade_horas_executadas'):
            for value in ('01:60', '12:99', '1.50', 'abc', '-01:00', '01:30:00'):
                form = SolicitacaoForm({**self.data, name: value})
                self.assertFalse(form.is_valid())
                self.assertIn(name, form.errors)

    def test_planning_fields_and_status_are_saved(self):
        self.client.force_login(self.user)
        data = {**self.data, 'quantidade_horas_executadas': '02:30',
                'data_inicio_planejamento': '2026-11-10', 'data_fim_planejamento': '2026-11-12',
                'status': 'EM_DESENVOLVIMENTO', 'situacao': 'EM_ANDAMENTO'}
        self.assertEqual(self.client.post(reverse('horas:abrir_solicitacao'), data).status_code, 302)
        item = Solicitacao.objects.get()
        self.assertEqual(str(item.quantidade_horas_executadas), '2.50')
        self.assertEqual(item.data_inicio_planejamento.isoformat(), '2026-11-10')
        self.assertEqual(item.data_fim_planejamento.isoformat(), '2026-11-12')
        self.assertEqual(item.status, 'EM_DESENVOLVIMENTO')
        self.assertEqual(item.situacao, 'EM_ANDAMENTO')

    def test_status_defaults_and_optional_fields(self):
        form = SolicitacaoForm(self.data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['status'], 'A_FAZER')
        self.assertEqual(SolicitacaoForm()['status'].value(), 'A_FAZER')
        self.assertIsNone(form.cleaned_data['quantidade_horas_executadas'])

    def test_invalid_planning_values(self):
        form = SolicitacaoForm({**self.data, 'quantidade_horas_executadas': '-1',
            'data_inicio_planejamento': '2026-11-12', 'data_fim_planejamento': '2026-11-10',
            'status': 'INVALIDO', 'situacao': 'INVALIDO'})
        self.assertFalse(form.is_valid())
        for field in ('quantidade_horas_executadas', 'data_fim_planejamento', 'status', 'situacao'):
            self.assertIn(field, form.errors)

    def test_save_free_estimate_without_agenda(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('horas:abrir_solicitacao'), self.data)
        self.assertEqual(response.status_code, 302)
        item = Solicitacao.objects.get()
        self.assertEqual(item.solicitante, self.user)
        self.assertEqual(item.orcamento, 'Livre')
        self.assertIsNone(item.orcamento_cadastrado)
        self.assertFalse(AgendaAtividade.objects.exists())

    def test_execution_uses_budget_data_despite_tampering(self):
        data = {**self.data, 'tipo': 'EXECUCAO', 'orcamento': '100', 'numero_chamado': 'fake'}
        form = SolicitacaoForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['pmo'], 'pmo')
        self.assertEqual(form.cleaned_data['cliente'], 'Cliente base')
        self.assertEqual(form.cleaned_data['codigo_cliente'], '10')
        self.assertEqual(form.cleaned_data['numero_chamado'], '123')
        self.assertEqual(form.instance.orcamento_cadastrado, self.budget)

    def test_estimate_preserves_manual_client_and_pmo(self):
        form = SolicitacaoForm({**self.data, 'orcamento': '100'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['cliente'], 'Novo cliente')
        self.assertEqual(form.cleaned_data['pmo'], 'PMO livre')
        self.assertEqual(form.cleaned_data['numero_chamado'], '123')

    def test_execution_missing_budget_data_is_rejected(self):
        self.budget.pmo = None
        self.budget.save()
        form = SolicitacaoForm({**self.data, 'tipo': 'EXECUCAO', 'orcamento': '100'})
        self.assertFalse(form.is_valid())
        self.assertIn('pmo', form.errors)

    def test_required_fields_and_positive_hours(self):
        form = SolicitacaoForm({})
        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), set(self.data))
        for value in ('0', '-1', 'abc'):
            form = SolicitacaoForm({**self.data, 'quantidade_horas': value})
            self.assertFalse(form.is_valid())
            self.assertIn('quantidade_horas', form.errors)

    def test_page_contains_search_data(self):
        Cliente.objects.create(Codigo_Cliente='10', Nome_Cliente='Cliente base')
        self.client.force_login(self.user)
        response = self.client.get(reverse('horas:abrir_solicitacao'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['solicitacao_pmos']), 1)
        self.assertEqual(response.context['solicitacao_clientes'][0]['codigo'], '10')
        self.assertContains(response, 'id_descritivo_necessidade')
        # The search must load with the page, including without collectstatic.
        self.assertContains(response, "input.addEventListener('input', render)")
        self.assertContains(response, 'normalize(o.label).includes(query)')
        self.assertNotContains(response, 'src="/static/js/abrir_solicitacao.js"')
        self.assertEqual(response.context['solicitacao_orcamentos'][0]['value'], '100')
