from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from .forms import SolicitacaoForm
from .models import AgendaAtividade, Solicitacao


class PainelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.pmo = User.objects.create_user(username='pmo1')
        self.other = User.objects.create_user(username='pmo2')
        self.admin = User.objects.create_user(username='administrador')
        self.regular = User.objects.create_user(username='comum')
        for user in (self.pmo, self.other):
            user.profile.is_pmo = True
            user.profile.save()
        self.admin.profile.is_administrador = True
        self.admin.profile.save()
        self.data = dict(orcamento='ORC-100', tipo='ESTIMATIVA', descricao='Demanda',
            pmo='pmo1', produto='ERP', codigo_cliente='10', cliente='Cliente teste',
            descritivo_necessidade='Necessidade', quantidade_horas='2', data_maxima_entrega='2026-12-01')
        self.item = Solicitacao.objects.create(**self.data, solicitante=self.regular,
            pmo_usuario=self.pmo, status='PLANEJADO')
        self.foreign = Solicitacao.objects.create(**{**self.data, 'orcamento': 'ORC-200', 'pmo': 'pmo2'},
            solicitante=self.regular, pmo_usuario=self.other)
        self.unassigned = Solicitacao.objects.create(**{**self.data, 'orcamento': 'ORC-300', 'pmo': 'Livre'},
            solicitante=self.regular)

    def test_pmo_sees_only_assigned_cards_in_correct_column(self):
        self.client.force_login(self.pmo)
        response = self.client.get(reverse('horas:painel'))
        self.assertContains(response, 'ORC-100')
        self.assertNotContains(response, 'ORC-200')
        self.assertNotContains(response, 'ORC-300')
        columns = response.context['colunas']
        self.assertEqual(len(columns), 6)
        self.assertEqual(columns[1]['solicitacoes'], [self.item])

    def test_card_opens_modal_with_details_and_only_two_editable_fields(self):
        self.client.force_login(self.pmo)
        response = self.client.get(reverse('horas:painel'))
        self.assertTemplateUsed(response, 'horas/_painel_solicitacao_modal.html')
        self.assertContains(response, f'data-solicitacao-modal-open="solicitacao-modal-{self.item.pk}"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'Necessidade')
        self.assertContains(response, 'Qtde de Horas Executadas')
        self.assertContains(response, 'Data Máxima de Entrega')
        self.assertContains(response, 'name="status"', count=1)
        self.assertContains(response, 'name="situacao"', count=1)
        self.assertNotContains(response, 'name="descricao"')
        self.assertNotContains(response, f'id="solicitacao-modal-{self.foreign.pk}"')
        html = response.content.decode()
        self.assertLess(html.index('href="' + reverse('horas:abrir_solicitacao') + '"'),
                        html.index('href="' + reverse('horas:painel') + '"'))

    def test_admin_sees_all_including_unassigned(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('horas:painel'))
        for value in ('ORC-100', 'ORC-200', 'ORC-300'):
            self.assertContains(response, value)

    def test_regular_user_has_no_cards_or_edit_access(self):
        self.client.force_login(self.regular)
        response = self.client.get(reverse('horas:painel'))
        self.assertNotContains(response, 'ORC-100')
        self.assertNotContains(response, 'class="kanban-card"')
        self.assertNotContains(response, 'href="/painel/"')
        self.assertEqual(self.client.get(reverse('horas:painel_solicitacao', args=[self.item.pk])).status_code, 404)

    def test_cannot_edit_another_pmos_card(self):
        self.client.force_login(self.pmo)
        url = reverse('horas:painel_solicitacao', args=[self.foreign.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'status': 'CONCLUIDO'}).status_code, 404)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.status, 'A_FAZER')

    def test_edit_moves_card_and_only_updates_allowed_fields(self):
        self.client.force_login(self.pmo)
        url = reverse('horas:painel_solicitacao', args=[self.item.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'status': 'EM_TESTES', 'situacao': 'AGUARDANDO_CLIENTE',
                                         'cliente': 'Tampered', 'pmo_usuario': self.other.pk})
        self.assertRedirects(response, reverse('horas:painel'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'EM_TESTES')
        self.assertEqual(self.item.situacao, 'AGUARDANDO_CLIENTE')
        self.assertEqual(self.item.cliente, 'Cliente teste')
        self.assertEqual(self.item.pmo_usuario, self.pmo)
        self.assertFalse(AgendaAtividade.objects.exists())

    def test_invalid_choices_do_not_update_card(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('horas:painel_solicitacao', args=[self.item.pk]),
                                    {'status': 'INVALIDO', 'situacao': 'INVALIDA'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('status', response.context['form'].errors)
        self.assertIn('situacao', response.context['form'].errors)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'PLANEJADO')

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(reverse('horas:painel')).status_code, 302)

    def test_form_resolves_identity_without_granting_ambiguous_access(self):
        form = SolicitacaoForm({**self.data, 'quantidade_horas': '02:00'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['pmo_usuario'], self.pmo)

    def test_legacy_pmo_linking_preserves_unmatched_as_unassigned(self):
        Solicitacao.objects.update(pmo_usuario=None)
        migration = import_module('horas.migrations.0037_solicitacao_pmo_usuario')
        migration.vincular_pmos(apps, connection.schema_editor(atomic=False))
        self.item.refresh_from_db()
        self.foreign.refresh_from_db()
        self.unassigned.refresh_from_db()
        self.assertEqual(self.item.pmo_usuario, self.pmo)
        self.assertEqual(self.foreign.pmo_usuario, self.other)
        self.assertIsNone(self.unassigned.pmo_usuario)
        for user in (self.pmo, self.other):
            user.first_name = 'Mesmo nome'
            user.save()
        form = SolicitacaoForm({**self.data, 'quantidade_horas': '02:00', 'pmo': 'Mesmo nome'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['pmo_usuario'])
        form = SolicitacaoForm({**self.data, 'quantidade_horas': '02:00', 'pmo': 'Mesmo nome', 'pmo_usuario': self.pmo.pk})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['pmo_usuario'], self.pmo)


    def test_unseen_badge_is_personal_and_cleared_by_panel(self):
        self.client.force_login(self.pmo)
        response = self.client.get(reverse('horas:abrir_solicitacao'))
        self.assertEqual(response.context['novas_solicitacoes_count'], 1)
        self.assertContains(response, 'nav-pending-badge')
        self.client.get(reverse('horas:planejamento'))
        self.item.refresh_from_db()
        self.assertFalse(self.item.vista_pelo_pmo)
        # An administrator viewing all requests must not clear the assigned PMO's alert.
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('horas:painel')).context['novas_solicitacoes_count'], 0)
        self.item.refresh_from_db()
        self.assertFalse(self.item.vista_pelo_pmo)
        self.client.force_login(self.pmo)
        response = self.client.get(reverse('horas:painel'))
        self.assertEqual(response.context['novas_solicitacoes_count'], 0)
        self.item.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertTrue(self.item.vista_pelo_pmo)
        self.assertFalse(self.foreign.vista_pelo_pmo)
        # Seen state survives logout and login.
        self.client.logout()
        self.client.force_login(self.pmo)
        self.assertEqual(self.client.get(reverse('horas:abrir_solicitacao')).context['novas_solicitacoes_count'], 0)

    def test_new_request_triggers_badge_again(self):
        self.client.force_login(self.pmo)
        self.client.get(reverse('horas:painel'))
        self.client.force_login(self.regular)
        response = self.client.post(reverse('horas:abrir_solicitacao'),
            {**self.data, 'quantidade_horas': '02:00', 'pmo_usuario': self.pmo.pk})
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.pmo)
        self.assertEqual(self.client.get(reverse('horas:abrir_solicitacao')).context['novas_solicitacoes_count'], 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('horas:abrir_solicitacao')).context['novas_solicitacoes_count'], 1)
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse('horas:abrir_solicitacao')).context['novas_solicitacoes_count'], 0)
