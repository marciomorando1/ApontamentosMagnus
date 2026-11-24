from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import SubTarefaForm
from .models import Solicitacao, SubTarefa


class PlanejamentoTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user(username='owner')
        self.pmo = users.objects.create_user(username='pmo')
        self.other = users.objects.create_user(username='other')
        self.admin = users.objects.create_superuser(username='admin', password='test')
        self.item = Solicitacao.objects.create(solicitante=self.owner, pmo_usuario=self.pmo,
            orcamento='100', tipo='ESTIMATIVA', descricao='Demanda', pmo='PMO original',
            produto='ERP', codigo_cliente='10', cliente='Cliente', descritivo_necessidade='Detalhes',
            quantidade_horas=10, data_maxima_entrega='2026-12-01')
        self.data = dict(tarefa='LEVANTAMENTO', descricao='Detalhes livres', quantidade_horas='00:01')
        self.url = reverse('horas:subtarefa_nova', args=[self.item.pk])
        self.client.force_login(self.owner)

    def test_create_edit_delete_and_exact_total(self):
        self.assertEqual(self.client.post(self.url, {**self.data, 'produto': 'HCM', 'pmo': 'Outro'}).status_code, 302)
        task = SubTarefa.objects.get()
        self.assertEqual(task.minutos, 1)
        self.client.post(self.url, {**self.data, 'quantidade_horas': '125:59'})
        response = self.client.get(reverse('horas:planejamento'))
        self.assertContains(response, '126:00')
        self.assertContains(response, 'PMO original')
        self.assertContains(response, 'ERP')
        edit = reverse('horas:subtarefa_editar', args=[self.item.pk, task.pk])
        self.assertContains(self.client.get(edit), '00:01')
        self.client.post(edit, {**self.data, 'quantidade_horas': '01:30'})
        task.refresh_from_db()
        self.assertEqual(task.minutos, 90)
        delete = reverse('horas:subtarefa_excluir', args=[self.item.pk, task.pk])
        self.assertEqual(self.client.get(delete).status_code, 200)
        self.assertTrue(SubTarefa.objects.filter(pk=task.pk).exists())
        self.assertEqual(self.client.post(delete).status_code, 302)
        self.assertFalse(SubTarefa.objects.filter(pk=task.pk).exists())
        self.assertContains(self.client.get(reverse('horas:planejamento')), '125:59')
        self.item.refresh_from_db()
        self.assertEqual(self.item.pmo, 'PMO original')
        self.assertEqual(self.item.produto, 'ERP')

    def test_invalid_duration_and_task(self):
        for value in ('00:00', '01:60', '-01:30', '1.5', 'abc', '01:30:00'):
            form = SubTarefaForm({**self.data, 'quantidade_horas': value})
            self.assertFalse(form.is_valid())
        self.assertEqual(self.client.post(self.url, {**self.data, 'tarefa': 'INVALIDO'}).status_code, 200)
        self.assertFalse(SubTarefa.objects.exists())

    def test_access_and_parent_binding(self):
        task = SubTarefa.objects.create(solicitacao=self.item, tarefa='LEVANTAMENTO', descricao='x', minutos=1)
        for user in (self.owner, self.pmo, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('horas:planejamento')), 'Demanda')
        self.assertEqual(self.client.post(self.url, self.data).status_code, 404)
        self.assertEqual(self.client.post(reverse('horas:subtarefa_excluir', args=[self.item.pk, task.pk])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_empty_total_and_menu_order(self):
        response = self.client.get(reverse('horas:planejamento'))
        self.assertContains(response, '00:00')
        html = response.content.decode()
        self.assertLess(html.index(reverse('horas:abrir_solicitacao')), html.index(reverse('horas:planejamento')))
