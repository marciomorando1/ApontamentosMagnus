from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import zipfile
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Estimativa, Fase, Orcamento, Registro


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

    def test_novo_registro_inicia_como_nao_processado(self):
        registro = Registro.objects.create(
            user=self.user,
            orcamento=self.orcamento,
            fase=self.fase,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='09:00',
        )
        self.assertEqual(registro.processado, Registro.PROCESSADO_NAO)


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

    def test_marca_registro_como_processado(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Pendente',
        )

        response = self.client.post(
            reverse('horas:registro_processar', args=[registro.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        registro.refresh_from_db()
        self.assertEqual(registro.processado, Registro.PROCESSADO_SIM)

    def test_desmarca_registro_processado(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Ja processado',
            processado=Registro.PROCESSADO_SIM,
        )

        response = self.client.post(
            reverse('horas:registro_processar', args=[registro.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        registro.refresh_from_db()
        self.assertEqual(registro.processado, Registro.PROCESSADO_NAO)

    def test_nao_permite_processar_registro_de_outro_usuario(self):
        registro = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Privado',
        )

        response = self.client.post(reverse('horas:registro_processar', args=[registro.pk]))

        self.assertEqual(response.status_code, 404)
        registro.refresh_from_db()
        self.assertEqual(registro.processado, Registro.PROCESSADO_NAO)


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


class EstimativasViewTests(AuthenticatedTestCase):
    def criar_estimativa(self, *, user=None):
        estimativa = Estimativa.objects.create(
            user=user or self.user,
            cliente='Cliente Teste',
            solicitante='Solicitante Teste',
            projeto='Chamado 123',
            sistema='ERP',
        )
        estimativa.itens.create(
            ordem=1,
            modulo_processo='Outros',
            recurso='Desenvolvedor',
            escopo='Criar campo customizado',
            horas_analise='1.50',
            horas_atividade='7.00',
        )
        return estimativa

    def test_cadastra_estimativa_com_item(self):
        response = self.client.post(
            reverse('horas:estimativa_nova'),
            data={
                'cliente': 'Magnus',
                'solicitante': 'Marcio',
                'projeto': 'Chamado 337531',
                'sistema': 'ERP',
                'itens-TOTAL_FORMS': '1',
                'itens-INITIAL_FORMS': '0',
                'itens-MIN_NUM_FORMS': '1',
                'itens-MAX_NUM_FORMS': '1000',
                'itens-0-ordem': '1',
                'itens-0-modulo_processo': 'Outros',
                'itens-0-recurso': 'Desenvolvedor',
                'itens-0-escopo': 'Criar regra de premio seguro',
                'itens-0-horas_analise': '01:30',
                'itens-0-horas_atividade': '11:00',
                'itens-0-horas_gp': '00:00',
                'itens-0-horas_estimadas': '00:00',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        estimativa = Estimativa.objects.get()
        self.assertEqual(estimativa.user, self.user)
        self.assertEqual(estimativa.itens.count(), 1)
        self.assertEqual(estimativa.itens.get().horas_estimadas, Decimal('12.5'))
        self.assertContains(response, 'Magnus')

    def test_nova_estimativa_abre_com_um_item_padrao(self):
        response = self.client.get(reverse('horas:estimativa_nova'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="itens-TOTAL_FORMS" value="1"', html=False)
        self.assertContains(response, '<select name="itens-0-modulo_processo"', html=False)
        self.assertContains(response, 'AP - Administração de Pessoal')
        self.assertContains(response, 'RS - Recrutamento e Seleção')
        self.assertContains(response, 'Gestão de Remuneração')
        self.assertContains(response, 'Outros')
        self.assertContains(response, '<select name="itens-0-recurso"', html=False)
        self.assertContains(response, 'Consultoria de Implantação')
        self.assertContains(response, 'Gerente de Projetos')
        self.assertContains(response, 'Desenvolvedor')
        self.assertContains(response, 'Analista de Infraestrutura')
        self.assertContains(response, 'Consultoria Especializada')
        self.assertContains(response, 'Análise da Demanda')
        self.assertContains(response, 'readonly', html=False)

    def test_edicao_nao_abre_linha_extra_vazia(self):
        estimativa = self.criar_estimativa()

        response = self.client.get(reverse('horas:estimativa_editar', args=[estimativa.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="itens-TOTAL_FORMS" value="1"', html=False)

    def test_lista_apenas_estimativas_do_usuario_logado(self):
        self.criar_estimativa()
        Estimativa.objects.create(
            user=self.other_user,
            cliente='Outro cliente',
            solicitante='Outro',
            projeto='Privado',
            sistema='HCM',
        )

        response = self.client.get(reverse('horas:estimativas'))

        self.assertContains(response, 'Cliente Teste')
        self.assertNotContains(response, 'Outro cliente')

    def test_filtra_estimativas_por_data_e_cliente(self):
        estimativa_magnus = self.criar_estimativa()
        estimativa_magnus.cliente = 'Magnus Sistemas'
        estimativa_magnus.criado_em = datetime(2026, 5, 10, 9, 0)
        estimativa_magnus.save(update_fields=['cliente', 'criado_em'])

        estimativa_antiga = Estimativa.objects.create(
            user=self.user,
            cliente='Magnus Antiga',
            solicitante='Outro',
            projeto='Chamado antigo',
            sistema='ERP',
        )
        estimativa_antiga.criado_em = datetime(2026, 4, 10, 9, 0)
        estimativa_antiga.save(update_fields=['criado_em'])

        Estimativa.objects.create(
            user=self.user,
            cliente='Outro Cliente',
            solicitante='Outro',
            projeto='Chamado fora',
            sistema='HCM',
        )

        response = self.client.get(
            reverse('horas:estimativas'),
            {'de': '2026-05-01', 'ate': '2026-05-31', 'cliente': 'Magnus'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Magnus Sistemas')
        self.assertNotContains(response, 'Magnus Antiga')
        self.assertNotContains(response, 'Outro Cliente')
        self.assertEqual(response.context['total_estimativas'], 1)

    def test_nao_permite_editar_estimativa_de_outro_usuario(self):
        estimativa = self.criar_estimativa(user=self.other_user)

        response = self.client.get(reverse('horas:estimativa_editar', args=[estimativa.pk]))

        self.assertEqual(response.status_code, 404)

    def test_salva_ignorando_linha_extra_vazia(self):
        response = self.client.post(
            reverse('horas:estimativa_nova'),
            data={
                'cliente': 'Magnus',
                'solicitante': 'Marcio',
                'projeto': 'Chamado 337531',
                'sistema': 'ERP',
                'itens-TOTAL_FORMS': '2',
                'itens-INITIAL_FORMS': '0',
                'itens-MIN_NUM_FORMS': '1',
                'itens-MAX_NUM_FORMS': '1000',
                'itens-0-ordem': '',
                'itens-0-modulo_processo': 'Outros',
                'itens-0-recurso': 'Desenvolvedor',
                'itens-0-escopo': 'Criar regra de premio seguro',
                'itens-0-horas_analise': '01:00',
                'itens-0-horas_atividade': '02:00',
                'itens-0-horas_gp': '00:00',
                'itens-0-horas_estimadas': '00:00',
                'itens-1-ordem': '2',
                'itens-1-modulo_processo': '',
                'itens-1-recurso': '',
                'itens-1-escopo': '',
                'itens-1-horas_analise': '00:00',
                'itens-1-horas_atividade': '00:00',
                'itens-1-horas_gp': '00:00',
                'itens-1-horas_estimadas': '00:00',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        estimativa = Estimativa.objects.get(cliente='Magnus')
        self.assertEqual(estimativa.itens.count(), 1)
        self.assertEqual(estimativa.itens.get().ordem, 1)

    def test_remove_item_existente_ao_salvar_estimativa(self):
        estimativa = self.criar_estimativa()
        item = estimativa.itens.get()

        response = self.client.post(
            reverse('horas:estimativa_editar', args=[estimativa.pk]),
            data={
                'cliente': estimativa.cliente,
                'solicitante': estimativa.solicitante,
                'projeto': estimativa.projeto,
                'sistema': estimativa.sistema,
                'itens-TOTAL_FORMS': '1',
                'itens-INITIAL_FORMS': '1',
                'itens-MIN_NUM_FORMS': '1',
                'itens-MAX_NUM_FORMS': '1000',
                'itens-0-id': item.pk,
                'itens-0-ordem': item.ordem,
                'itens-0-modulo_processo': item.modulo_processo,
                'itens-0-recurso': item.recurso,
                'itens-0-escopo': item.escopo,
                'itens-0-horas_analise': item.horas_analise_formatado,
                'itens-0-horas_atividade': item.horas_atividade_formatado,
                'itens-0-horas_gp': item.horas_gp_formatado,
                'itens-0-horas_estimadas': item.horas_estimadas_formatado,
                'itens-0-DELETE': 'on',
            },
        )

        self.assertRedirects(response, reverse('horas:estimativas'), fetch_redirect_response=False)
        self.assertFalse(estimativa.itens.exists())

    def test_exporta_estimativa_xlsx_preenchida(self):
        estimativa = self.criar_estimativa()

        response = self.client.get(reverse('horas:estimativa_exportar', args=[estimativa.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn(
            'filename="EHM DEVS - Cliente Teste - Chamado 123.xlsx"',
            response['Content-Disposition'],
        )

        with zipfile.ZipFile(BytesIO(response.content), 'r') as workbook_zip:
            workbook = ET.fromstring(workbook_zip.read('xl/workbook.xml'))
            rels = ET.fromstring(workbook_zip.read('xl/_rels/workbook.xml.rels'))
            ns = {
                'm': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
            }
            relmap = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels}
            sheet_path = None
            for sheet in workbook.findall('m:sheets/m:sheet', ns):
                if 'Escopo' in sheet.attrib['name']:
                    sheet_path = 'xl/' + relmap[sheet.attrib[f"{{{ns['r']}}}id"]].lstrip('/').replace('../', '')
                    break
            sheet_root = ET.fromstring(workbook_zip.read(sheet_path))
            sheet_xml = ET.tostring(sheet_root, encoding='unicode')

            def cell_value(ref):
                cell = sheet_root.find(f".//m:c[@r='{ref}']", ns)
                if cell is None:
                    return None
                value = cell.find('m:v', ns)
                return value.text if value is not None else None

        self.assertIn('Cliente Teste', sheet_xml)
        self.assertIn('Criar campo customizado', sheet_xml)
        self.assertAlmostEqual(float(cell_value('C19')), 9.5 / 24)
        self.assertAlmostEqual(float(cell_value('C20')), 2392.0)
        self.assertAlmostEqual(float(cell_value('C21')), 2035.0)
        self.assertAlmostEqual(float(cell_value('C24')), 1 / 24)
        self.assertAlmostEqual(float(cell_value('H24')), 250.0)
        self.assertAlmostEqual(float(cell_value('C25')), 7.0 / 24)
        self.assertAlmostEqual(float(cell_value('H25')), 1785.0)
        self.assertAlmostEqual(float(cell_value('C28')), 1.5 / 24)
        self.assertAlmostEqual(float(cell_value('H28')), 357.0)
        self.assertEqual(float(cell_value('H37')), 8.5 / 24)
        self.assertIsNone(cell_value('E38'))
        self.assertIsNone(cell_value('F38'))
        self.assertIsNone(cell_value('G38'))
        self.assertIsNone(cell_value('H38'))


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

