from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Fase, Orcamento, Registro


User = get_user_model()


class AuthenticatedTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='senha-segura')
        self.other_user = User.objects.create_user(username='other', password='senha-segura')
        self.client.force_login(self.user)
        self.fase = Fase.objects.create(codigo='101', descricao='Comercial - Venda')

    def criar_registro(self, *, orcamento, fase=None, user=None, **kwargs):
        defaults = {
            'user': user or self.user,
            'orcamento': orcamento,
            'fase': fase or self.fase,
            'data': date.today(),
            'hora_inicio': '08:00',
            'hora_fim': '09:00',
            'descricao': 'Atividade',
        }
        defaults.update(kwargs)
        return Registro.objects.create(**defaults)


class RegistroModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='model-user', password='senha-segura')
        self.orcamento = Orcamento.objects.create(codigo='17275', nome='Projeto teste')
        self.fase = Fase.objects.create(codigo='101', descricao='Comercial - Venda')

    def test_rejeita_data_futura(self):
        registro = Registro(
            user=self.user,
            orcamento=self.orcamento,
            fase=self.fase,
            data=date.today() + timedelta(days=1),
            hora_inicio='08:00',
            hora_fim='09:00',
        )
        with self.assertRaises(ValidationError):
            registro.full_clean()

    def test_rejeita_hora_final_menor_ou_igual(self):
        registro = Registro(
            user=self.user,
            orcamento=self.orcamento,
            fase=self.fase,
            data=date.today(),
            hora_inicio='10:00',
            hora_fim='10:00',
        )
        with self.assertRaises(ValidationError):
            registro.full_clean()

    def test_rejeita_registro_sem_fase(self):
        registro = Registro(
            user=self.user,
            orcamento=self.orcamento,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='09:00',
        )
        with self.assertRaises(ValidationError):
            registro.full_clean()


class TimerViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.orcamento = Orcamento.objects.create(codigo='17275', nome='Projeto teste')

    def test_cria_registro_valido(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:30',
                'descricao': 'Implementação inicial',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Registro.objects.count(), 1)
        self.assertEqual(Registro.objects.get().user, self.user)

    def test_salva_varios_registros_manuais_no_mesmo_envio(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'submission_mode': 'manual',
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:00',
                'descricao': 'Primeira atividade',
                'extra_hora_inicio': ['09:30', '14:00'],
                'extra_hora_fim': ['10:30', '15:15'],
                'extra_descricao': ['Segunda atividade', 'Terceira atividade'],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Registro.objects.count(), 3)
        self.assertEqual(Registro.objects.filter(user=self.user).count(), 3)

    def test_nao_cria_registro_sem_fase(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:30',
                'descricao': 'Sem fase',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Registro.objects.count(), 0)
        self.assertContains(response, 'Selecione')


class RegistrosViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.orcamento = Orcamento.objects.create(codigo='17275', nome='Projeto teste')

    def test_lista_registros_em_ordem_crescente_por_data(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 8),
            hora_inicio='10:00',
            hora_fim='11:00',
            descricao='Mais recente',
        )
        self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 1),
            hora_inicio='08:00',
            hora_fim='09:00',
            descricao='Mais antigo',
        )

        response = self.client.get(
            reverse('horas:registros'),
            {'de': '2026-04-01', 'ate': '2026-04-30'},
        )

        self.assertEqual(response.status_code, 200)
        registros = list(response.context['registros'])
        self.assertEqual(registros[0].descricao, 'Mais antigo')
        self.assertEqual(registros[1].descricao, 'Mais recente')

    def test_registros_carrega_filtros_com_data_atual_por_padrao(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='09:00',
            descricao='Hoje',
        )
        self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 1),
            hora_inicio='08:00',
            hora_fim='09:00',
            descricao='Outro dia',
        )

        response = self.client.get(reverse('horas:registros'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filtros']['de'], date.today().isoformat())
        self.assertEqual(response.context['filtros']['ate'], date.today().isoformat())
        registros = list(response.context['registros'])
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].descricao, 'Hoje')

    def test_atualiza_registro_salvo(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 8),
            hora_inicio='10:00',
            hora_fim='11:00',
            descricao='Descrição original',
        )

        response = self.client.post(
            reverse('horas:registro_editar', args=[registro.pk]),
            data={
                'data': '2026-04-08',
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '10:30',
                'hora_fim': '11:45',
                'descricao': 'Descrição alterada',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        registro.refresh_from_db()
        self.assertEqual(registro.hora_inicio.isoformat(timespec='minutes'), '10:30')
        self.assertEqual(registro.hora_fim.isoformat(timespec='minutes'), '11:45')
        self.assertEqual(registro.descricao, 'Descrição alterada')

    def test_formulario_edicao_mantem_data_preenchida(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 8),
            hora_inicio='10:00',
            hora_fim='11:00',
            descricao='Com data preenchida',
        )

        response = self.client.get(reverse('horas:registro_editar', args=[registro.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="2026-04-08"', html=False)

    def test_permite_editar_mesmo_registro_mais_de_uma_vez(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 8),
            hora_inicio='10:00',
            hora_fim='11:00',
            descricao='Versao inicial',
        )

        primeira_resposta = self.client.post(
            reverse('horas:registro_editar', args=[registro.pk]),
            data={
                'data': '2026-04-08',
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '10:15',
                'hora_fim': '11:15',
                'descricao': 'Primeira edicao',
            },
            follow=True,
        )

        segunda_resposta = self.client.post(
            reverse('horas:registro_editar', args=[registro.pk]),
            data={
                'data': '2026-04-08',
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '10:30',
                'hora_fim': '11:30',
                'descricao': 'Segunda edicao',
            },
            follow=True,
        )

        self.assertEqual(primeira_resposta.status_code, 200)
        self.assertEqual(segunda_resposta.status_code, 200)
        registro.refresh_from_db()
        self.assertEqual(registro.hora_inicio.isoformat(timespec='minutes'), '10:30')
        self.assertEqual(registro.hora_fim.isoformat(timespec='minutes'), '11:30')
        self.assertEqual(registro.descricao, 'Segunda edicao')

    def test_edicao_preserva_filtros_no_retorno(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 8),
            hora_inicio='10:00',
            hora_fim='11:00',
            descricao='Teste filtros',
        )

        response = self.client.post(
            f"{reverse('horas:registro_editar', args=[registro.pk])}?de=2026-04-01&ate=2026-04-30&orcamento={self.orcamento.pk}",
            data={
                'data': '2026-04-08',
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '10:00',
                'hora_fim': '11:30',
                'descricao': 'Teste filtros',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('horas:registros')}?de=2026-04-01&ate=2026-04-30&orcamento={self.orcamento.pk}",
            fetch_redirect_response=False,
        )

    def test_lista_apenas_registros_do_usuario_logado(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Meu registro',
        )
        self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Registro de outro usuário',
        )

        response = self.client.get(reverse('horas:registros'))

        registros = list(response.context['registros'])
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].descricao, 'Meu registro')

    def test_nao_permite_editar_registro_de_outro_usuario(self):
        registro = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Privado',
        )

        response = self.client.get(reverse('horas:registro_editar', args=[registro.pk]))

        self.assertEqual(response.status_code, 404)

    def test_nao_permite_remover_registro_de_outro_usuario(self):
        registro = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Privado',
        )

        response = self.client.post(reverse('horas:registro_remover', args=[registro.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Registro.objects.filter(pk=registro.pk).exists())


class ResumoViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.orcamento = Orcamento.objects.create(codigo='17275', nome='Projeto teste')

    def test_resumo_carrega_filtros_com_data_atual_por_padrao(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='09:00',
            descricao='Hoje',
        )
        self.criar_registro(
            orcamento=self.orcamento,
            data=date(2026, 4, 1),
            hora_inicio='08:00',
            hora_fim='09:00',
            descricao='Outro dia',
        )

        response = self.client.get(reverse('horas:resumo'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filtros']['de'], date.today().isoformat())
        self.assertEqual(response.context['filtros']['ate'], date.today().isoformat())
        self.assertEqual(response.context['stats'][1][1], 1)

    def test_resumo_considera_apenas_registros_do_usuario_logado(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='10:00',
            descricao='Meu resumo',
        )
        self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='12:00',
            descricao='Resumo de outro usuário',
        )

        response = self.client.get(reverse('horas:resumo'))

        self.assertEqual(response.context['stats'][0][1], '2h00')
        self.assertEqual(response.context['stats'][1][1], 1)
        self.assertEqual(len(response.context['detalhes_orcamento']), 1)


class AuthenticationFlowTests(TestCase):
    def test_redireciona_para_login_quando_nao_autenticado(self):
        response = self.client.get(reverse('horas:timer'))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('horas:timer')}")


class FasesViewTests(AuthenticatedTestCase):
    def test_lista_fases_cadastradas(self):
        fase = Fase.objects.create(codigo='102', descricao='Comercial - Pós-Venda')

        response = self.client.get(reverse('horas:fases'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, fase.codigo)
        self.assertContains(response, fase.descricao)

    def test_cadastra_fase_valida(self):
        response = self.client.post(
            reverse('horas:fases'),
            data={
                'codigo': '202',
                'descricao': 'Construção e Modelagem',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Fase.objects.filter(codigo='202', descricao='Construção e Modelagem').exists()
        )

    def test_remove_fase(self):
        fase = Fase.objects.create(codigo='299', descricao='Horas Fora do Escopo')

        response = self.client.post(
            reverse('horas:fase_remover', args=[fase.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Fase.objects.filter(pk=fase.pk).exists())

