from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import zipfile
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import AgendaAtividade, Estimativa, Fase, Orcamento, Registro, UserProfile


User = get_user_model()


def build_test_xlsx(rows):
    worksheet = ET.Element(
        '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}worksheet'
    )
    sheet_data = ET.SubElement(
        worksheet,
        '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData',
    )
    for row_number, values in enumerate(rows, start=1):
        row = ET.SubElement(
            sheet_data,
            '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row',
            {'r': str(row_number)},
        )
        for column_number, value in enumerate(values, start=1):
            column = ''
            number = column_number
            while number:
                number, remainder = divmod(number - 1, 26)
                column = chr(65 + remainder) + column
            cell = ET.SubElement(
                row,
                '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c',
                {'r': f'{column}{row_number}', 't': 'inlineStr'},
            )
            inline = ET.SubElement(
                cell,
                '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is',
            )
            ET.SubElement(
                inline,
                '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t',
            ).text = str(value)

    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            'xl/worksheets/sheet1.xml',
            ET.tostring(worksheet, encoding='utf-8', xml_declaration=True),
        )
        workbook.writestr(
            'xl/workbook.xml',
            '''<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Orcamentos" sheetId="1" r:id="rId1"/></sheets>
            </workbook>''',
        )
        workbook.writestr(
            'xl/_rels/workbook.xml.rels',
            '''<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
            </Relationships>''',
        )
    output.seek(0)
    return output


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

    def test_abre_apontamento_com_data_e_orcamento_preenchidos(self):
        data_apontamento = date(2026, 6, 10)

        response = self.client.get(
            reverse('horas:timer'),
            {'data': data_apontamento.isoformat(), 'orcamento': self.orcamento.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['data'], data_apontamento)
        self.assertEqual(response.context['form'].initial['orcamento'], str(self.orcamento.pk))
        self.assertContains(response, 'value="2026-06-10"', html=False)
        self.assertContains(response, f'<option value="{self.orcamento.pk}" selected>', html=False)

    def test_apontamento_preenche_orcamento_da_agenda_mesmo_se_inativo(self):
        self.orcamento.ativo = False
        self.orcamento.save(update_fields=['ativo'])

        response = self.client.get(
            reverse('horas:timer'),
            {'data': '2026-06-10', 'orcamento': self.orcamento.pk},
        )

        self.assertContains(response, f'<option value="{self.orcamento.pk}" selected>', html=False)

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


class OrcamentosViewTests(AuthenticatedTestCase):
    headers_importacao = [
        'orcamento',
        'cliente',
        'chamado',
        'descricao',
    ]

    def importar_planilha(self, rows, filename='orcamentos.xlsx'):
        arquivo = SimpleUploadedFile(
            filename,
            build_test_xlsx(rows).getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        return self.client.post(
            reverse('horas:orcamentos'),
            data={'action': 'importar', 'arquivo': arquivo},
        )

    def test_cadastra_orcamento_com_codigo_cliente_e_numero_chamado(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '100',
                'codigo_cliente': '200',
                'numero_chamado': '300',
                'nome': 'Projeto com chamado',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        orcamento = Orcamento.objects.get(codigo='100')
        self.assertEqual(orcamento.codigo_cliente, '200')
        self.assertEqual(orcamento.numero_chamado, '300')
        self.assertContains(response, '<td class="mono">200</td>', html=True)
        self.assertContains(response, '<td class="mono">300</td>', html=True)

    def test_lista_orcamentos_em_linhas_com_acoes(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            codigo_cliente='2001',
            numero_chamado='3001',
            nome='Projeto em linha',
        )

        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, '<table>', html=False)
        self.assertNotContains(response, 'class="orc-grid"', html=False)
        self.assertContains(response, 'Projeto em linha')
        self.assertContains(response, reverse('horas:orcamento_editar', args=[orcamento.pk]))
        self.assertContains(response, reverse('horas:orcamento_remover', args=[orcamento.pk]))

    def test_rejeita_letras_nos_campos_numericos(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': 'ORC-100',
                'codigo_cliente': 'CLI-200',
                'numero_chamado': 'CH-300',
                'nome': 'Projeto inválido',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Orcamento.objects.filter(nome='Projeto inválido').exists())
        self.assertFormError(response.context['form'], 'codigo', 'Informe somente números.')
        self.assertFormError(response.context['form'], 'codigo_cliente', 'Informe somente números.')
        self.assertFormError(response.context['form'], 'numero_chamado', 'Informe somente números.')

    def test_formulario_configura_teclado_numerico(self):
        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, 'name="codigo" inputmode="numeric"', html=False)
        self.assertContains(response, 'name="codigo_cliente" inputmode="numeric"', html=False)
        self.assertContains(response, 'name="numero_chamado" inputmode="numeric"', html=False)
        self.assertContains(response, 'numeric-only', count=4)
        self.assertContains(response, "field.value.replace(/\\D/g, '')", html=False)
        self.assertContains(response, 'event.preventDefault()', html=False)

    def test_permite_orcamento_legado_sem_novos_campos(self):
        orcamento = Orcamento.objects.create(codigo='ORC-LEGADO', nome='Legado')

        self.assertEqual(orcamento.codigo_cliente, '')
        self.assertEqual(orcamento.numero_chamado, '')

    def test_importa_orcamentos_de_planilha_xlsx(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A'],
                ['1002', '2002', '3002', 'Projeto B'],
            ]
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        self.assertTrue(
            Orcamento.objects.filter(
                codigo='1001',
                codigo_cliente='2001',
                numero_chamado='3001',
                nome='Projeto A',
            ).exists()
        )
        self.assertTrue(Orcamento.objects.filter(codigo='1002').exists())

    def test_importacao_rejeita_cabecalhos_fora_de_ordem(self):
        response = self.importar_planilha(
            [
                ['Código Cliente', 'Código Orçamento', 'Número do Chamado', 'Descrição'],
                ['2001', '1001', '3001', 'Projeto A'],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Os cabeçalhos devem estar nesta ordem')
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_importacao_rejeita_orcamento_ja_existente_na_base(self):
        Orcamento.objects.create(codigo='1001', nome='Existente')

        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Duplicado'],
                ['1002', '2002', '3002', 'Novo'],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'o orçamento 1001 já existe na base')
        self.assertFalse(Orcamento.objects.filter(codigo='1002').exists())

    def test_importacao_rejeita_orcamento_duplicado_na_planilha(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A'],
                ['1001', '2002', '3002', 'Projeto B'],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'o orçamento 1001 está duplicado na planilha')
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_importacao_rejeita_campos_numericos_invalidos(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['ORC-1', 'CLI-1', 'CH-1', 'Inválido'],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Código Orçamento deve conter somente números')
        self.assertContains(response, 'Código Cliente deve conter somente números')
        self.assertContains(response, 'Número do Chamado deve conter somente números')
        self.assertEqual(Orcamento.objects.count(), 0)

    def test_lista_exibe_opcao_editar_orcamento(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Projeto')

        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, reverse('horas:orcamento_editar', args=[orcamento.pk]))

    def test_formulario_edicao_carrega_dados_do_orcamento(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            codigo_cliente='2001',
            numero_chamado='3001',
            nome='Projeto original',
        )

        response = self.client.get(reverse('horas:orcamento_editar', args=[orcamento.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="1001"', html=False)
        self.assertContains(response, 'value="2001"', html=False)
        self.assertContains(response, 'value="3001"', html=False)
        self.assertContains(response, 'value="Projeto original"', html=False)

    def test_edita_orcamento(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Projeto original')

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': '1002',
                'codigo_cliente': '2002',
                'numero_chamado': '3002',
                'nome': 'Projeto atualizado',
            },
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.codigo, '1002')
        self.assertEqual(orcamento.codigo_cliente, '2002')
        self.assertEqual(orcamento.numero_chamado, '3002')
        self.assertEqual(orcamento.nome, 'Projeto atualizado')

    def test_edicao_rejeita_codigo_duplicado(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Primeiro')
        Orcamento.objects.create(codigo='1002', nome='Segundo')

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': '1002',
                'codigo_cliente': '2001',
                'numero_chamado': '3001',
                'nome': 'Duplicado',
            },
        )

        self.assertEqual(response.status_code, 200)
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.codigo, '1001')
        self.assertFormError(
            response.context['form'],
            'codigo',
            'Já existe um orçamento com este código.',
        )

    def test_edicao_rejeita_letras_nos_campos_numericos(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Projeto')

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': 'ORC-1',
                'codigo_cliente': 'CLI-1',
                'numero_chamado': 'CH-1',
                'nome': 'Inválido',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'codigo', 'Informe somente números.')
        self.assertFormError(response.context['form'], 'codigo_cliente', 'Informe somente números.')
        self.assertFormError(response.context['form'], 'numero_chamado', 'Informe somente números.')


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


class UserProfileTests(TestCase):
    def test_cria_profile_para_novo_usuario(self):
        user = User.objects.create_user(username='novo-perfil', password='senha-segura')

        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertFalse(user.profile.is_gerente_projetos)


class AgendaAtividadeModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='agenda-model', password='senha-segura')
        self.gp = User.objects.create_user(username='agenda-gp', password='senha-segura')
        self.gp.profile.is_gerente_projetos = True
        self.gp.profile.save(update_fields=['is_gerente_projetos'])
        self.orcamento = Orcamento.objects.create(codigo='9001', nome='Agenda Orcamento')

    def test_rejeita_data_final_menor_que_inicial(self):
        atividade = AgendaAtividade(
            user=self.user,
            criado_por=self.gp,
            cliente='Cliente',
            numero_chamado='123',
            orcamento=self.orcamento,
            produto='Produto',
            titulo='Atividade',
            descricao='Descricao',
            data_inicio=date(2026, 6, 10),
            data_fim=date(2026, 6, 9),
        )

        with self.assertRaises(ValidationError):
            atividade.full_clean()

    def test_rejeita_hora_final_menor_ou_igual_no_mesmo_dia(self):
        atividade = AgendaAtividade(
            user=self.user,
            criado_por=self.gp,
            cliente='Cliente',
            orcamento=self.orcamento,
            titulo='Atividade',
            data_inicio=date(2026, 6, 10),
            hora_inicio='10:00',
            data_fim=date(2026, 6, 10),
            hora_fim='09:00',
            total_horas_maximo=Decimal('2'),
        )

        with self.assertRaises(ValidationError):
            atividade.full_clean()

    def test_permite_hora_final_menor_em_atividade_de_varios_dias(self):
        atividade = AgendaAtividade(
            user=self.user,
            criado_por=self.gp,
            cliente='Cliente',
            orcamento=self.orcamento,
            titulo='Atividade',
            data_inicio=date(2026, 6, 10),
            hora_inicio='18:00',
            data_fim=date(2026, 6, 11),
            hora_fim='09:00',
            total_horas_maximo=Decimal('4'),
        )

        atividade.full_clean()

    def test_rejeita_total_horas_maximo_igual_a_zero(self):
        atividade = AgendaAtividade(
            user=self.user,
            criado_por=self.gp,
            cliente='Cliente',
            orcamento=self.orcamento,
            titulo='Atividade',
            data_inicio=date(2026, 6, 10),
            hora_inicio='08:00',
            data_fim=date(2026, 6, 10),
            hora_fim='09:00',
            total_horas_maximo=Decimal('0'),
        )

        with self.assertRaises(ValidationError):
            atividade.full_clean()

    def test_permite_atividade_legada_sem_horarios_e_total_maximo(self):
        atividade = AgendaAtividade(
            user=self.user,
            criado_por=self.gp,
            cliente='Cliente',
            orcamento=self.orcamento,
            titulo='Atividade legada',
            data_inicio=date(2026, 6, 10),
            data_fim=date(2026, 6, 10),
        )

        atividade.full_clean()


class AgendaViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='agenda-user', password='senha-segura')
        self.other_user = User.objects.create_user(username='agenda-other', password='senha-segura')
        self.gp = User.objects.create_user(username='agenda-gp', password='senha-segura')
        self.gp.profile.is_gerente_projetos = True
        self.gp.profile.save(update_fields=['is_gerente_projetos'])
        self.orcamento = Orcamento.objects.create(
            codigo='9002',
            codigo_cliente='12345',
            numero_chamado='67890',
            nome='Agenda Orcamento',
        )

    def criar_atividade(self, *, user, criado_por, titulo='Atividade', data_inicio=None, data_fim=None):
        return AgendaAtividade.objects.create(
            user=user,
            criado_por=criado_por,
            cliente='Cliente Agenda',
            numero_chamado='CH-1',
            orcamento=self.orcamento,
            produto='ERP',
            titulo=titulo,
            descricao='Descricao da atividade',
            data_inicio=data_inicio or date(2026, 6, 10),
            hora_inicio='08:00',
            data_fim=data_fim or date(2026, 6, 12),
            hora_fim='17:00',
            total_horas_maximo=Decimal('16'),
        )

    def test_usuario_comum_ve_apenas_propria_agenda(self):
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Minha atividade')
        self.criar_atividade(user=self.other_user, criado_por=self.other_user, titulo='Atividade de outro')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertEqual(response.status_code, 200)
        atividades = list(response.context['agenda_atividades'])
        self.assertEqual(len(atividades), 1)
        self.assertEqual(atividades[0].titulo, 'Minha atividade')

    def test_gerente_sem_filtro_ve_instrucao_e_lista_vazia(self):
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_agenda_empty_filter'])
        self.assertContains(response, 'Selecione um usuário')

    def test_gerente_filtra_e_ve_agenda_de_outro_usuario(self):
        self.criar_atividade(user=self.user, criado_por=self.gp, titulo='Planejamento GP')
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06', 'usuario': self.user.pk})

        self.assertEqual(response.status_code, 200)
        atividades = list(response.context['agenda_atividades'])
        self.assertEqual(len(atividades), 1)
        self.assertEqual(atividades[0].titulo, 'Planejamento GP')

    def test_gerente_cria_atividade_para_outro_usuario(self):
        self.client.force_login(self.gp)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'user': self.user.pk,
                'cliente': 'Cliente Agenda',
                'numero_chamado': 'CH-100',
                'orcamento': self.orcamento.pk,
                'produto': 'ERP',
                'titulo': 'Atividade delegada',
                'descricao': 'Descricao',
                'data_inicio': '2026-06-10',
                'hora_inicio': '08:30',
                'data_fim': '2026-06-12',
                'hora_fim': '17:30',
                'total_horas_maximo': '20:30',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade = AgendaAtividade.objects.get(titulo='Atividade delegada')
        self.assertEqual(atividade.user, self.user)
        self.assertEqual(atividade.criado_por, self.gp)
        self.assertEqual(atividade.cliente, self.orcamento.codigo_cliente)
        self.assertEqual(atividade.numero_chamado, self.orcamento.numero_chamado)
        self.assertEqual(atividade.hora_inicio.isoformat(timespec='minutes'), '08:30')
        self.assertEqual(atividade.hora_fim.isoformat(timespec='minutes'), '17:30')
        self.assertEqual(atividade.total_horas_maximo, Decimal('20.5'))

    def test_usuario_comum_cria_atividade_na_propria_agenda(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'numero_chamado': 'CH-200',
                'orcamento': self.orcamento.pk,
                'produto': 'ERP',
                'titulo': 'Minha atividade',
                'descricao': 'Descricao',
                'data_inicio': '2026-06-10',
                'hora_inicio': '09:00',
                'data_fim': '2026-06-11',
                'hora_fim': '18:00',
                'total_horas_maximo': '08:00',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade = AgendaAtividade.objects.get(titulo='Minha atividade')
        self.assertEqual(atividade.user, self.user)
        self.assertEqual(atividade.criado_por, self.user)
        self.assertEqual(atividade.cliente, self.orcamento.codigo_cliente)
        self.assertEqual(atividade.numero_chamado, self.orcamento.numero_chamado)

    def test_total_horas_maximo_aceita_digitos_sem_separador(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'orcamento': self.orcamento.pk,
                'produto': 'ERP',
                'titulo': 'Duracao compacta',
                'data_inicio': '2026-06-10',
                'hora_inicio': '09:00',
                'data_fim': '2026-06-10',
                'hora_fim': '17:00',
                'total_horas_maximo': '0600',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade = AgendaAtividade.objects.get(titulo='Duracao compacta')
        self.assertEqual(atividade.total_horas_maximo, Decimal('6'))

    def test_formulario_posiciona_orcamento_antes_do_cliente_e_compacta_horarios(self):
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:agenda_nova'))
        content = response.content.decode()

        self.assertLess(content.index('name="user"'), content.index('name="orcamento"'))
        self.assertLess(content.index('name="orcamento"'), content.index('name="cliente"'))
        self.assertContains(response, 'class="agenda-schedule-row"', html=False)
        self.assertContains(response, 'maskDurationInput', html=False)

    def test_nova_atividade_bloqueia_e_preenche_cliente_e_chamado_pelo_orcamento(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_nova'))

        self.assertContains(response, 'name="cliente" maxlength="200" readonly="readonly"', html=False)
        self.assertContains(response, 'name="numero_chamado" maxlength="100" readonly="readonly"', html=False)
        self.assertContains(response, 'data-cliente="12345"', html=False)
        self.assertContains(response, 'data-chamado="67890"', html=False)
        self.assertContains(response, 'fillBudgetData', html=False)

    def test_nova_atividade_ignora_cliente_e_chamado_enviados_manualmente(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': '99999',
                'numero_chamado': '99999',
                'orcamento': self.orcamento.pk,
                'produto': 'ERP',
                'titulo': 'Dados protegidos',
                'data_inicio': '2026-06-10',
                'hora_inicio': '09:00',
                'data_fim': '2026-06-10',
                'hora_fim': '17:00',
                'total_horas_maximo': '08:00',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade = AgendaAtividade.objects.get(titulo='Dados protegidos')
        self.assertEqual(atividade.cliente, '12345')
        self.assertEqual(atividade.numero_chamado, '67890')

    def test_edicao_mantem_cliente_e_chamado_liberados(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.user)
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_editar', args=[atividade.pk]))

        self.assertNotContains(response, 'name="cliente" maxlength="200" readonly="readonly"', html=False)
        self.assertNotContains(response, 'name="numero_chamado" maxlength="100" readonly="readonly"', html=False)

    def test_formulario_produto_exibe_apenas_erp_e_hcm(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_nova'))
        produto = response.context['form'].fields['produto']

        self.assertEqual(list(produto.choices), [('', '— selecione —'), ('ERP', 'ERP'), ('HCM', 'HCM')])
        self.assertTrue(produto.required)

    def test_rejeita_produto_fora_da_lista(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'orcamento': self.orcamento.pk,
                'produto': 'Outro',
                'titulo': 'Produto invalido',
                'data_inicio': '2026-06-10',
                'hora_inicio': '09:00',
                'data_fim': '2026-06-10',
                'hora_fim': '17:00',
                'total_horas_maximo': '08:00',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AgendaAtividade.objects.filter(titulo='Produto invalido').exists())

    def test_formulario_edicao_preserva_produto_legado(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.user)
        atividade.produto = 'Produto legado'
        atividade.save(update_fields=['produto'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_editar', args=[atividade.pk]))

        self.assertContains(response, '<option value="Produto legado" selected>Produto legado</option>', html=True)

    def test_usuario_comum_nao_edita_atividade_criada_por_gerente(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.gp, titulo='Delegada')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_editar', args=[atividade.pk]))

        self.assertEqual(response.status_code, 404)

    def test_gerente_edita_atividade_criada_para_terceiro(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.gp, titulo='Delegada')
        self.client.force_login(self.gp)

        response = self.client.post(
            reverse('horas:agenda_editar', args=[atividade.pk]),
            data={
                'user': self.user.pk,
                'cliente': atividade.cliente,
                'numero_chamado': atividade.numero_chamado,
                'orcamento': atividade.orcamento.pk,
                'produto': atividade.produto,
                'titulo': 'Delegada ajustada',
                'descricao': atividade.descricao,
                'data_inicio': atividade.data_inicio.isoformat(),
                'hora_inicio': '10:00',
                'data_fim': atividade.data_fim.isoformat(),
                'hora_fim': '19:00',
                'total_horas_maximo': '24:30',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade.refresh_from_db()
        self.assertEqual(atividade.titulo, 'Delegada ajustada')
        self.assertEqual(atividade.hora_inicio.isoformat(timespec='minutes'), '10:00')
        self.assertEqual(atividade.total_horas_maximo, Decimal('24.5'))

    def test_rejeita_nova_atividade_sem_horarios_e_total_maximo(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'orcamento': self.orcamento.pk,
                'titulo': 'Sem planejamento',
                'data_inicio': '2026-06-10',
                'data_fim': '2026-06-10',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AgendaAtividade.objects.filter(titulo='Sem planejamento').exists())
        self.assertFormError(response.context['form'], 'hora_inicio', 'Este campo é obrigatório.')
        self.assertFormError(response.context['form'], 'hora_fim', 'Este campo é obrigatório.')
        self.assertFormError(response.context['form'], 'total_horas_maximo', 'Este campo é obrigatório.')

    def test_calendario_exibe_horarios_e_total_maximo(self):
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Com planejamento')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, '08:00 - 17:00')
        self.assertContains(response, 'Máx. 16:00')

    def test_card_agenda_exibe_botao_apontar_com_data_e_orcamento(self):
        atividade = self.criar_atividade(
            user=self.user,
            criado_por=self.user,
            titulo='Apontar atividade',
            data_inicio=date(2026, 6, 10),
            data_fim=date(2026, 6, 12),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        for data_card in ('2026-06-10', '2026-06-11', '2026-06-12'):
            self.assertContains(
                response,
                f'{reverse("horas:timer")}?data={data_card}&orcamento={atividade.orcamento_id}',
            )
        self.assertContains(response, '>Apontar</a>', count=3, html=False)

    def test_card_agenda_exibe_botao_editar_quando_usuario_pode_gerenciar(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.user, titulo='Editável')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, reverse('horas:agenda_editar', args=[atividade.pk]))
        self.assertContains(response, '>Editar</a>', count=3, html=False)

    def test_card_agenda_nao_exibe_botao_editar_sem_permissao(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.gp, titulo='Delegada')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertNotContains(response, reverse('horas:agenda_editar', args=[atividade.pk]))

    def test_calendario_ordena_atividades_por_hora_inicial(self):
        tarde = self.criar_atividade(user=self.user, criado_por=self.user, titulo='Atividade tarde')
        tarde.hora_inicio = '14:00'
        tarde.save(update_fields=['hora_inicio'])
        manha = self.criar_atividade(user=self.user, criado_por=self.user, titulo='Atividade manha')
        manha.hora_inicio = '08:00'
        manha.save(update_fields=['hora_inicio'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})
        day_items = next(
            day['atividades']
            for week in response.context['agenda_weeks']
            for day in week
            if day['date'] == date(2026, 6, 10)
        )

        self.assertEqual([item.titulo for item in day_items], ['Atividade manha', 'Atividade tarde'])

    def test_calendario_mostra_atividade_em_todos_os_dias_do_intervalo(self):
        self.criar_atividade(
            user=self.user,
            criado_por=self.user,
            titulo='Faixa completa',
            data_inicio=date(2026, 6, 10),
            data_fim=date(2026, 6, 12),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertEqual(response.status_code, 200)
        weeks = response.context['agenda_weeks']
        matches = []
        for week in weeks:
            for day in week:
                if day['date'] in {date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)}:
                    matches.append(len(day['atividades']))
        self.assertEqual(matches, [1, 1, 1])

    def test_filtro_de_mes_mantem_usuario_na_navegacao(self):
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06', 'usuario': self.user.pk})

        self.assertContains(response, f'mes=2026-05&usuario={self.user.pk}')
        self.assertContains(response, f'mes=2026-07&usuario={self.user.pk}')

    def test_formulario_edicao_mantem_remocao_em_form_separado(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.user)
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_editar', args=[atividade.pk]))
        content = response.content.decode()

        self.assertContains(response, 'id="agenda-save-form"', html=False)
        self.assertContains(response, 'id="agenda-remove-form"', html=False)
        self.assertLess(
            content.index('</form>'),
            content.index('id="agenda-remove-form"'),
        )

    def test_remove_atividade_da_agenda(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.user)
        self.client.force_login(self.user)

        response = self.client.post(reverse('horas:agenda_remover', args=[atividade.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AgendaAtividade.objects.filter(pk=atividade.pk).exists())

    def test_formulario_exibe_campo_usuario_apenas_para_gerente(self):
        self.client.force_login(self.gp)
        response_gp = self.client.get(reverse('horas:agenda_nova'))
        self.assertContains(response_gp, 'name="user"')

        self.client.force_login(self.user)
        response_user = self.client.get(reverse('horas:agenda_nova'))
        self.assertNotContains(response_user, '<label for="id_user">Usuário</label>', html=False)

