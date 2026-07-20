from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import zipfile
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse, set_script_prefix

from .models import (
    AgendaAtividade,
    Cliente,
    Estimativa,
    Fase,
    FolgaFeriado,
    Orcamento,
    Registro,
    Servico,
    SolicitacaoHoras,
    UserProfile,
)


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
            cell_attrs = {'r': f'{column}{row_number}', 't': 'inlineStr'}
            cell_value = value
            if isinstance(value, dict):
                cell_value = value.get('value', '')
                cell_attrs.update(value.get('attrs', {}))
            cell = ET.SubElement(
                row,
                '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c',
                cell_attrs,
            )
            if cell_attrs.get('t') == 'inlineStr':
                inline = ET.SubElement(
                    cell,
                    '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is',
                )
                ET.SubElement(
                    inline,
                    '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t',
                ).text = str(cell_value)
            else:
                ET.SubElement(
                    cell,
                    '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v',
                ).text = str(cell_value)

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
        workbook.writestr(
            'xl/styles.xml',
            '''<?xml version="1.0" encoding="UTF-8"?>
            <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <numFmts count="1"><numFmt numFmtId="164" formatCode="[h]:mm"/></numFmts>
              <cellXfs count="2"><xf numFmtId="0" xfId="0"/><xf numFmtId="164" xfId="0" applyNumberFormat="1"/></cellXfs>
            </styleSheet>''',
        )
    output.seek(0)
    return output


class AuthenticatedTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='senha-segura')
        self.other_user = User.objects.create_user(username='other', password='senha-segura')
        self.client.force_login(self.user)
        self.fase = Fase.objects.create(codigo='101', descricao='Comercial - Venda')
        self.servico = Servico.objects.create(codigo='S01', descricao='Implantação')

    def criar_registro(self, *, orcamento, fase=None, user=None, **kwargs):
        if orcamento.horas == 0:
            orcamento.horas = Decimal('1000')
            orcamento.save(update_fields=['horas'])
        defaults = {
            'user': user or self.user,
            'orcamento': orcamento,
            'fase': fase or self.fase,
            'servico': self.servico,
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
        self.orcamento = Orcamento.objects.create(codigo='17275', nome='Projeto teste', horas='1000')
        self.fase = Fase.objects.create(codigo='101', descricao='Comercial - Venda')
        self.servico = Servico.objects.create(codigo='S01', descricao='Implantação')

    def test_rejeita_data_futura(self):
        registro = Registro(
            user=self.user,
            orcamento=self.orcamento,
            fase=self.fase,
            servico=self.servico,
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
            servico=self.servico,
            data=date.today(),
            hora_inicio='10:00',
            hora_fim='10:00',
        )
        with self.assertRaises(ValidationError):
            registro.full_clean()

    def test_permite_registro_sem_fase(self):
        registro = Registro(
            user=self.user,
            orcamento=self.orcamento,
            servico=self.servico,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='09:00',
        )
        registro.full_clean()
        self.assertIsNone(registro.fase)

    def test_novo_registro_inicia_como_nao_processado(self):
        registro = Registro.objects.create(
            user=self.user,
            orcamento=self.orcamento,
            fase=self.fase,
            servico=self.servico,
            data=date.today(),
            hora_inicio='08:00',
            hora_fim='09:00',
        )
        self.assertEqual(registro.processado, Registro.PROCESSADO_NAO)


class TimerViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.orcamento = Orcamento.objects.create(
            codigo='17275',
            nome='Projeto teste',
            horas=Decimal('20.5'),
        )

    def test_cria_registro_valido(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
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
        self.assertContains(
            response,
            f'<option value="{self.orcamento.pk}" selected',
            html=False,
        )
        self.assertContains(response, 'data-horas-apontadas="00:00"', html=False)
        self.assertContains(response, 'data-horas-disponiveis="20:30"', html=False)

    def test_abre_apontamento_com_servico_preenchido(self):
        response = self.client.get(
            reverse('horas:timer'),
            {'servico': self.servico.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['servico'], str(self.servico.pk))
        self.assertContains(response, f'<option value="{self.servico.pk}" selected', html=False)

    def test_exibe_horas_do_orcamento_em_campo_somente_leitura(self):
        response = self.client.get(reverse('horas:timer'))

        self.assertContains(response, 'id="orcamento-horas"', html=False)
        self.assertContains(response, 'id="orcamento-horas" placeholder="—" readonly', html=False)
        self.assertContains(response, 'data-horas="20:30"', html=False)
        self.assertContains(response, 'function updateBudgetHours()', html=False)
        self.assertContains(response, "budgetField.addEventListener('change', updateBudgetHours)", html=False)

    def test_base_autenticada_exibe_alternancia_de_tema(self):
        response = self.client.get(reverse('horas:timer'))

        self.assertContains(response, 'data-theme-toggle', html=False)
        self.assertContains(response, 'Tema claro')
        self.assertContains(response, "localStorage.getItem('horas-theme')", html=False)
        self.assertContains(response, "document.documentElement.setAttribute('data-theme', 'light')", html=False)
        self.assertContains(response, "document.documentElement.removeAttribute('data-theme')", html=False)

    def test_apontamento_preenche_orcamento_da_agenda_mesmo_se_inativo(self):
        self.orcamento.ativo = False
        self.orcamento.save(update_fields=['ativo'])

        response = self.client.get(
            reverse('horas:timer'),
            {'data': '2026-06-10', 'orcamento': self.orcamento.pk},
        )

        self.assertContains(
            response,
            f'<option value="{self.orcamento.pk}" selected',
            html=False,
        )

    def test_salva_varios_registros_manuais_no_mesmo_envio(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'submission_mode': 'manual',
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
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

    def test_cria_registro_sem_fase(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:30',
                'descricao': 'Sem fase',
            },
        )
        self.assertRedirects(response, reverse('horas:timer'), fetch_redirect_response=False)
        self.assertEqual(Registro.objects.count(), 1)
        self.assertIsNone(Registro.objects.get().fase)

    def test_nao_cria_registro_sem_servico(self):
        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:30',
                'descricao': 'Sem serviço',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Registro.objects.count(), 0)
        self.assertContains(response, 'Selecione um serviço.')

    def test_apontamento_contabiliza_horas_no_orcamento_independente_do_usuario(self):
        self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:30',
                'descricao': 'Primeiro usuário',
            },
        )
        self.client.force_login(self.other_user)
        self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '10:00',
                'hora_fim': '11:00',
                'descricao': 'Segundo usuário',
            },
        )

        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('2.50'))
        self.assertEqual(self.orcamento.horas_disponiveis, Decimal('18.00'))

    def test_bloqueia_apontamento_que_excede_horas_disponiveis(self):
        self.orcamento.horas = Decimal('2')
        self.orcamento.responsavel = self.other_user
        self.orcamento.save(update_fields=['horas', 'responsavel'])

        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '11:00',
                'descricao': 'Excede saldo',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'excede as horas disponíveis')
        self.assertContains(response, self.other_user.username)
        self.assertEqual(Registro.objects.count(), 0)
        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('0'))

    def test_multiplos_apontamentos_sao_bloqueados_sem_consumir_saldo_parcial(self):
        self.orcamento.horas = Decimal('2')
        self.orcamento.save(update_fields=['horas'])

        response = self.client.post(
            reverse('horas:timer'),
            data={
                'submission_mode': 'manual',
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '09:30',
                'descricao': 'Dentro do saldo',
                'extra_hora_inicio': ['10:00'],
                'extra_hora_fim': ['11:00'],
                'extra_descricao': ['Excede no conjunto'],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'excede as horas disponíveis')
        self.assertEqual(Registro.objects.count(), 0)
        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('0'))

    def test_remover_apontamento_devolve_horas_ao_orcamento(self):
        self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '10:00',
                'descricao': 'Será removido',
            },
        )
        registro = Registro.objects.get()

        self.client.post(reverse('horas:registro_remover', args=[registro.pk]))

        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('0'))
        self.assertEqual(self.orcamento.horas_disponiveis, Decimal('20.50'))

    def test_timer_exibe_horas_apontadas_e_disponiveis(self):
        self.orcamento.horas_apontadas = Decimal('4.50')
        self.orcamento.save(update_fields=['horas_apontadas'])

        response = self.client.get(reverse('horas:timer'))

        self.assertContains(response, 'Quantidade de Horas Apontadas')
        self.assertContains(response, 'Quantidade de Horas Disponíveis')
        self.assertContains(response, 'data-horas-apontadas="04:30"', html=False)
        self.assertContains(response, 'data-horas-disponiveis="16:00"', html=False)


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

    def test_lista_registros_exibe_servico(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Com serviço',
        )

        response = self.client.get(reverse('horas:registros'))

        self.assertContains(response, '<th>Serviço</th>', html=False)
        self.assertContains(response, self.servico.codigo)

    def test_lista_registros_exibe_fase_quando_preenchida(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Com fase',
        )

        response = self.client.get(reverse('horas:registros'))

        self.assertContains(response, '<th>Fase</th>', html=False)
        self.assertContains(response, self.fase.codigo)

    def test_lista_registros_sem_fase_sem_bloquear_listagem(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Sem fase',
        )
        registro.fase = None
        registro.save(update_fields=['fase'])

        response = self.client.get(reverse('horas:registros'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sem fase')

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
                'servico': self.servico.pk,
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

    def test_edicao_recalcula_horas_apontadas_do_orcamento(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            hora_inicio='08:00',
            hora_fim='09:00',
        )
        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('1.00'))

        response = self.client.post(
            reverse('horas:registro_editar', args=[registro.pk]),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '10:30',
                'descricao': 'Duracao atualizada',
            },
        )

        self.assertRedirects(response, reverse('horas:registros'), fetch_redirect_response=False)
        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('2.50'))

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
                'servico': self.servico.pk,
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
                'servico': self.servico.pk,
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
                'servico': self.servico.pk,
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
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Meu registro',
        )
        registro_exportado = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Registro de outro usuário',
        )

        response = self.client.get(reverse('horas:registros'))

        registros = list(response.context['registros'])
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].descricao, 'Meu registro')

    def test_usuario_sem_exportacsv_nao_ve_filtro_usuario_nem_lista_outros(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Meu registro',
        )
        registro_exportado = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Registro de outro usuario',
        )

        response = self.client.get(
            reverse('horas:registros'),
            {'usuario': self.other_user.pk},
        )

        registros = list(response.context['registros'])
        self.assertFalse(response.context['can_filter_usuario'])
        self.assertNotContains(response, 'name="usuario"', html=False)
        self.assertNotContains(response, 'Exportar CSV')
        self.assertNotContains(response, reverse('horas:registro_processar', args=[registro.pk]))
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].descricao, 'Meu registro')

    def test_usuario_com_exportacsv_filtra_registros_por_usuario(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Meu registro',
        )
        outro_registro = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Registro de outro usuario',
        )

        response = self.client.get(
            reverse('horas:registros'),
            {'usuario': self.other_user.pk},
        )

        registros = list(response.context['registros'])
        self.assertTrue(response.context['can_filter_usuario'])
        self.assertContains(response, 'name="usuario"', html=False)
        self.assertContains(response, 'Exportar CSV')
        self.assertContains(response, reverse('horas:registro_processar', args=[outro_registro.pk]))
        self.assertContains(response, self.other_user.username)
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].descricao, 'Registro de outro usuario')

    def test_csv_respeita_filtro_usuario_quando_exportacsv_habilitado(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        self.other_user.profile.codigoerp = 9876
        self.other_user.profile.save(update_fields=['codigoerp'])
        self.orcamento.numero_chamado = 'CH-123'
        self.orcamento.save(update_fields=['numero_chamado'])
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Meu registro',
        )
        registro_exportado = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Registro de outro usuario',
        )

        response = self.client.get(
            reverse('horas:exportar_csv'),
            {'usuario': self.other_user.pk},
        )
        content = response.content.decode('utf-8-sig')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'Consultor;Orcamento;Servico;Data;Hora Inicio;Hora Fim;Descr Atividade;Nr Chamado;Cod. Fase',
            content,
        )
        self.assertIn(
            f'9876;{self.orcamento.codigo};{self.servico.codigo};{date.today().strftime("%d/%m/%Y")};08:00;09:00;Registro de outro usuario;CH-123;{self.fase.codigo}',
            content,
        )
        self.assertIn('Registro de outro usuario', content)
        self.assertNotIn('Meu registro', content)
        registro_exportado.refresh_from_db()
        self.assertEqual(registro_exportado.processado, Registro.PROCESSADO_SIM)

    def test_csv_exporta_apenas_registros_nao_processados(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Pendente para exportar',
        )
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Ja exportado',
            processado=Registro.PROCESSADO_SIM,
        )

        response = self.client.get(reverse('horas:exportar_csv'))
        content = response.content.decode('utf-8-sig')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Pendente para exportar', content)
        self.assertNotIn('Ja exportado', content)

    def test_csv_sem_registros_pendentes_nao_gera_arquivo(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Ja exportado',
            processado=Registro.PROCESSADO_SIM,
        )

        response = self.client.get(reverse('horas:exportar_csv'))

        self.assertRedirects(response, reverse('horas:registros'), fetch_redirect_response=False)

    def test_usuario_sem_exportacsv_nao_acessa_exportacao_csv(self):
        self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Pendente',
        )

        response = self.client.get(reverse('horas:exportar_csv'))

        self.assertEqual(response.status_code, 403)

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

    def test_registro_processado_aparece_somente_para_visualizacao(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Somente leitura',
            processado=Registro.PROCESSADO_SIM,
        )

        response = self.client.get(reverse('horas:registros'))

        self.assertContains(response, 'Somente leitura')
        self.assertNotContains(response, reverse('horas:registro_processar', args=[registro.pk]))
        self.assertNotContains(response, reverse('horas:registro_editar', args=[registro.pk]))
        self.assertNotContains(response, reverse('horas:registro_remover', args=[registro.pk]))

    def test_nao_permite_editar_ou_remover_registro_processado(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Processado',
            processado=Registro.PROCESSADO_SIM,
        )

        response_editar = self.client.get(reverse('horas:registro_editar', args=[registro.pk]))
        response_remover = self.client.post(reverse('horas:registro_remover', args=[registro.pk]))

        self.assertEqual(response_editar.status_code, 403)
        self.assertEqual(response_remover.status_code, 403)
        self.assertTrue(Registro.objects.filter(pk=registro.pk).exists())

    def test_marca_registro_como_processado(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
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

    def test_nao_desmarca_registro_processado(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Ja processado',
            processado=Registro.PROCESSADO_SIM,
        )

        response = self.client.post(
            reverse('horas:registro_processar', args=[registro.pk]),
        )

        self.assertEqual(response.status_code, 403)
        registro.refresh_from_db()
        self.assertEqual(registro.processado, Registro.PROCESSADO_SIM)

    def test_nao_permite_processar_registro_sem_exportacsv(self):
        registro = self.criar_registro(
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Privado',
        )

        response = self.client.post(reverse('horas:registro_processar', args=[registro.pk]))

        self.assertEqual(response.status_code, 403)
        registro.refresh_from_db()
        self.assertEqual(registro.processado, Registro.PROCESSADO_NAO)

    def test_usuario_com_exportacsv_processa_registro_de_outro_usuario(self):
        self.user.profile.exportacsv = True
        self.user.profile.save(update_fields=['exportacsv'])
        registro = self.criar_registro(
            user=self.other_user,
            orcamento=self.orcamento,
            data=date.today(),
            descricao='Privado',
        )

        response = self.client.post(reverse('horas:registro_processar', args=[registro.pk]))

        self.assertRedirects(response, reverse('horas:registros'), fetch_redirect_response=False)
        registro.refresh_from_db()
        self.assertEqual(registro.processado, Registro.PROCESSADO_SIM)


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

    def test_usuario_sem_troca_obrigatoria_acessa_sistema_normalmente(self):
        User.objects.create_user(username='sem-troca', password='SenhaForte123!')

        response = self.client.post(
            reverse('login'),
            data={'username': 'sem-troca', 'password': 'SenhaForte123!'},
        )

        self.assertRedirects(response, reverse('horas:timer'), fetch_redirect_response=False)

    def test_usuario_com_troca_obrigatoria_redireciona_apos_login(self):
        user = User.objects.create_user(username='com-troca', password='SenhaForte123!')
        user.profile.must_change_password = True
        user.profile.save(update_fields=['must_change_password'])

        response = self.client.post(
            reverse('login'),
            data={'username': 'com-troca', 'password': 'SenhaForte123!'},
        )

        self.assertRedirects(response, reverse('password_change_required'), fetch_redirect_response=False)

    def test_usuario_com_troca_obrigatoria_nao_acessa_telas_do_sistema(self):
        user = User.objects.create_user(username='bloqueado', password='SenhaForte123!')
        user.profile.must_change_password = True
        user.profile.save(update_fields=['must_change_password'])
        self.client.force_login(user)

        response = self.client.get(reverse('horas:agenda'))

        self.assertRedirects(response, reverse('password_change_required'), fetch_redirect_response=False)

    def test_troca_com_senha_atual_invalida_mantem_bloqueio(self):
        user = User.objects.create_user(username='senha-invalida', password='SenhaForte123!')
        user.profile.must_change_password = True
        user.profile.save(update_fields=['must_change_password'])
        self.client.force_login(user)

        response = self.client.post(
            reverse('password_change_required'),
            data={
                'old_password': 'errada',
                'new_password1': 'NovaSenhaForte123!',
                'new_password2': 'NovaSenhaForte123!',
            },
        )

        user.profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(user.profile.must_change_password)
        self.assertContains(response, 'Senha atual')

    def test_troca_valida_altera_senha_desmarca_flag_e_mantem_sessao(self):
        user = User.objects.create_user(username='troca-valida', password='SenhaForte123!')
        user.profile.must_change_password = True
        user.profile.save(update_fields=['must_change_password'])
        self.client.force_login(user)

        response = self.client.post(
            reverse('password_change_required'),
            data={
                'old_password': 'SenhaForte123!',
                'new_password1': 'NovaSenhaForte123!',
                'new_password2': 'NovaSenhaForte123!',
            },
        )

        user.refresh_from_db()
        user.profile.refresh_from_db()
        self.assertRedirects(response, reverse('horas:timer'), fetch_redirect_response=False)
        self.assertFalse(user.profile.must_change_password)
        self.assertTrue(user.check_password('NovaSenhaForte123!'))
        self.assertFalse(self.client.login(username='troca-valida', password='SenhaForte123!'))
        self.assertTrue(self.client.login(username='troca-valida', password='NovaSenhaForte123!'))

    @override_settings(FORCE_SCRIPT_NAME='/apontamentos', STATIC_URL='/apontamentos/static/')
    def test_troca_de_senha_aceita_prefixo_apontamentos(self):
        set_script_prefix('/apontamentos/')
        self.addCleanup(set_script_prefix, '/')
        user = User.objects.create_user(username='prefixo-senha', password='SenhaForte123!')
        user.profile.must_change_password = True
        user.profile.save(update_fields=['must_change_password'])
        self.client.force_login(user)

        response = self.client.get('/apontamentos/senha/trocar/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Troque sua senha')

    def test_admin_exibe_flag_de_troca_obrigatoria(self):
        admin = User.objects.create_superuser(
            username='admin-senhas',
            password='SenhaForte123!',
            email='admin@example.com',
        )
        user = User.objects.create_user(username='usuario-admin', password='SenhaForte123!')
        self.client.force_login(admin)

        response = self.client.get(reverse('admin:auth_user_change', args=[user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'must_change_password')
        self.assertContains(response, 'is_administrador')
        self.assertContains(response, 'is_pmo')
        self.assertContains(response, 'exportacsv')
        self.assertContains(response, 'codigoerp')

    def test_admin_exige_codigoerp_ao_criar_usuario(self):
        admin = User.objects.create_superuser(
            username='admin-codigoerp',
            password='SenhaForte123!',
            email='admin-codigoerp@example.com',
        )
        self.client.force_login(admin)
        url = reverse('admin:auth_user_add')

        response_sem_codigo = self.client.post(
            url,
            data={
                'username': 'usuario-sem-codigo',
                'password1': 'SenhaForte123!abc',
                'password2': 'SenhaForte123!abc',
            },
        )

        self.assertEqual(response_sem_codigo.status_code, 200)
        self.assertFalse(User.objects.filter(username='usuario-sem-codigo').exists())

        response_com_codigo = self.client.post(
            url,
            data={
                'username': 'usuario-com-codigo',
                'password1': 'SenhaForte123!abc',
                'password2': 'SenhaForte123!abc',
                'codigoerp': '12345',
            },
        )

        self.assertEqual(response_com_codigo.status_code, 302)
        user = User.objects.get(username='usuario-com-codigo')
        self.assertEqual(user.profile.codigoerp, 12345)
        self.assertFalse(user.profile.is_administrador)

    def test_admin_pode_marcar_usuario_como_administrador_ao_criar(self):
        admin = User.objects.create_superuser(
            username='admin-cria-administrador',
            password='SenhaForte123!',
            email='admin-cria-administrador@example.com',
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse('admin:auth_user_add'),
            data={
                'username': 'usuario-administrador',
                'password1': 'SenhaForte123!abc',
                'password2': 'SenhaForte123!abc',
                'codigoerp': '54321',
                'is_administrador': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='usuario-administrador')
        self.assertEqual(user.profile.codigoerp, 54321)
        self.assertTrue(user.profile.is_administrador)


class OrcamentosViewTests(AuthenticatedTestCase):
    headers_importacao = [
        'orcamento',
        'cliente',
        'chamado',
        'descricao',
        'qtd_horas',
        'pmo',
    ]

    def setUp(self):
        super().setUp()
        self.user.profile.is_gerente_projetos = True
        self.user.profile.save(update_fields=['is_gerente_projetos'])
        self.pmo_user = User.objects.create_user(username='pmo-user', password='senha-segura')
        self.pmo_user.profile.is_pmo = True
        self.pmo_user.profile.save(update_fields=['is_pmo'])
        self.non_pmo_user = User.objects.create_user(username='non-pmo-user', password='senha-segura')
        for codigo, nome in {
            '200': 'Cliente Teste',
            '201': 'Cliente Horas',
            '202': 'Cliente PMO',
            '203': 'Cliente Minutos',
            '204': 'Cliente Muitas Horas',
            '2001': 'Cliente Original',
            '2002': 'Cliente Atualizado',
            '12345': 'Cliente Admin',
        }.items():
            Cliente.objects.update_or_create(
                Codigo_Cliente=codigo,
                defaults={
                    'Nome_Cliente': nome,
                    'Situacao': Cliente.SITUACAO_ATIVO,
                    'Usuario_Alteracao': self.user,
                },
            )

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
                'nome_cliente': 'Cliente Teste',
                'numero_chamado': '300',
                'nome': 'Projeto com chamado',
                'horas': '12:30',
                'pmo': self.pmo_user.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        orcamento = Orcamento.objects.get(codigo='100')
        self.assertEqual(orcamento.codigo_cliente, '200')
        self.assertEqual(orcamento.nome_cliente, 'Cliente Teste')
        self.assertEqual(orcamento.numero_chamado, '300')
        self.assertEqual(orcamento.horas, Decimal('12.50'))
        self.assertEqual(orcamento.responsavel, self.user)
        self.assertEqual(orcamento.pmo, self.pmo_user)
        self.assertContains(response, '<td class="mono">200</td>', html=True)
        self.assertContains(response, '<td>Cliente Teste</td>', html=True)
        self.assertContains(response, '<td class="mono">300</td>', html=True)
        self.assertContains(response, '12:30')
        self.assertContains(response, self.user.username)
        self.assertContains(response, self.pmo_user.username)
        self.assertContains(response, 'Orçamento adicionado com sucesso.')

    def test_cadastra_orcamento_com_minutos_fracionarios(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '103',
                'codigo_cliente': '203',
                'numero_chamado': '303',
                'nome': 'Projeto com minutos quebrados',
                'horas': '22:50',
                'pmo': self.pmo_user.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        orcamento = Orcamento.objects.get(codigo='103')
        self.assertEqual(orcamento.horas, Decimal('22.83'))
        self.assertEqual(orcamento.horas_formatadas, '22:50')
        self.assertContains(response, '22:50')

    def test_cadastra_orcamento_com_horas_acima_de_quatro_digitos(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '104',
                'codigo_cliente': '204',
                'numero_chamado': '304',
                'nome': 'Projeto com muitas horas',
                'horas': '12345:15',
                'pmo': self.pmo_user.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        orcamento = Orcamento.objects.get(codigo='104')
        self.assertEqual(orcamento.horas, Decimal('12345.25'))
        self.assertEqual(orcamento.horas_formatadas, '12345:15')

    def test_campo_pmo_lista_apenas_usuarios_marcados_como_pmo(self):
        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, f'<option value="{self.pmo_user.pk}">pmo-user</option>', html=True)
        self.assertNotContains(response, f'<option value="{self.non_pmo_user.pk}">non-pmo-user</option>', html=True)

    def test_rejeita_usuario_nao_pmo_no_orcamento(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '102',
                'codigo_cliente': '202',
                'numero_chamado': '302',
                'nome': 'Projeto PMO invalido',
                'horas': '12:00',
                'pmo': self.non_pmo_user.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'],
            'pmo',
            'Faça uma escolha válida. Sua escolha não é uma das disponíveis.',
        )
        self.assertFalse(Orcamento.objects.filter(codigo='102').exists())

    def test_quantidade_horas_aceita_digitos_sem_separador(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '101',
                'codigo_cliente': '201',
                'numero_chamado': '301',
                'nome': 'Projeto com horas compactas',
                'horas': '2000',
            },
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        orcamento = Orcamento.objects.get(codigo='101')
        self.assertEqual(orcamento.horas, Decimal('20'))
        self.assertEqual(orcamento.horas_formatadas, '20:00')

    def test_quantidade_horas_rejeita_minutos_invalidos(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '101',
                'codigo_cliente': '201',
                'numero_chamado': '301',
                'nome': 'Projeto inválido',
                'horas': '20:60',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'],
            'horas',
            'Informe as horas no formato HH:MM.',
        )
        self.assertFalse(Orcamento.objects.filter(codigo='101').exists())

    def test_lista_orcamentos_em_linhas_com_acoes(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            codigo_cliente='2001',
            numero_chamado='3001',
            nome_cliente='Cliente Linha',
            nome='Projeto em linha',
            responsavel=self.user,
        )

        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, '<table class="orcamentos-table">', html=False)
        self.assertNotContains(response, 'class="orc-grid"', html=False)
        self.assertContains(response, 'Cliente Linha')
        self.assertContains(response, 'Projeto em linha')
        self.assertContains(response, reverse('horas:orcamento_editar', args=[orcamento.pk]))
        self.assertContains(response, reverse('horas:orcamento_remover', args=[orcamento.pk]))

    def test_lista_orcamentos_usa_tabela_compacta_sem_flex_no_td_de_acoes(self):
        Orcamento.objects.create(
            codigo='1001',
            nome='Projeto em linha',
            responsavel=self.user,
        )

        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, 'class="table-wrap orcamentos-table-wrap"', html=False)
        self.assertContains(response, 'class="orcamentos-table"', html=False)
        self.assertContains(response, 'id="orcamentos-grid"', html=False)
        self.assertContains(response, 'action="/orcamentos/#orcamentos-grid"', html=False)
        self.assertContains(response, '<colgroup>', html=False)
        self.assertContains(response, 'class="orcamento-actions"', html=False)
        self.assertNotContains(response, '<td class="actions-cell">', html=False)

    def test_filtra_orcamentos_por_codigo(self):
        Orcamento.objects.create(codigo='1001', nome='Projeto Alfa', responsavel=self.user)
        Orcamento.objects.create(codigo='2002', nome='Projeto Beta', responsavel=self.user)

        response = self.client.get(reverse('horas:orcamentos'), {'codigo': '100'})

        self.assertEqual([orcamento.codigo for orcamento in response.context['orcamentos']], ['1001'])
        self.assertContains(response, 'name="codigo" type="text" value="100"', html=False)
        self.assertContains(response, 'href="/orcamentos/#orcamentos-grid"', html=False)
        self.assertContains(response, 'Projeto Alfa')
        self.assertNotContains(response, 'Projeto Beta')

    def test_filtra_orcamentos_por_descricao(self):
        Orcamento.objects.create(codigo='1001', nome='Implantação Financeira', responsavel=self.user)
        Orcamento.objects.create(codigo='2002', nome='Sustentação Comercial', responsavel=self.user)

        response = self.client.get(reverse('horas:orcamentos'), {'descricao': 'financeira'})

        self.assertEqual([orcamento.codigo for orcamento in response.context['orcamentos']], ['1001'])
        self.assertContains(response, 'name="descricao" type="text" value="financeira"', html=False)
        self.assertContains(response, 'Implantação Financeira')
        self.assertNotContains(response, 'Sustentação Comercial')

    def test_rejeita_letras_nos_campos_numericos(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': 'ORC-100',
                'codigo_cliente': 'CLI-200',
                'numero_chamado': 'CH-300',
                'horas': '10:00',
                'nome': 'Projeto inválido',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Orcamento.objects.filter(nome='Projeto inválido').exists())
        self.assertFormError(response.context['form'], 'codigo', 'Informe somente números.')
        self.assertIn('escolh', str(response.context['form'].errors['codigo_cliente']))
        self.assertFormError(response.context['form'], 'numero_chamado', 'Informe somente números.')

    def test_formulario_configura_teclado_numerico(self):
        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, 'name="codigo" inputmode="numeric"', html=False)
        self.assertContains(response, 'name="codigo_cliente" data-cliente-select="true"', html=False)
        self.assertContains(response, '200 - Cliente Teste')
        self.assertContains(response, 'name="nome_cliente" readonly="readonly"', html=False)
        self.assertContains(response, 'data-cliente-nome="true"', html=False)
        self.assertContains(response, 'name="numero_chamado" inputmode="numeric"', html=False)
        self.assertContains(response, 'name="horas"', html=False)
        self.assertContains(response, 'data-compact-duration="true"', html=False)
        self.assertNotContains(response, 'maxlength="7"', html=False)
        self.assertContains(response, 'pattern="^\\d+:[0-5]\\d$"', html=False)
        self.assertContains(response, 'numeric-only', count=3)
        self.assertContains(response, "field.value.replace(/\\D/g, '')", html=False)
        self.assertContains(response, 'event.preventDefault()', html=False)
        self.assertContains(response, 'function maskCompactDuration(input)', html=False)
        self.assertContains(response, 'digits.slice(0, -2)', html=False)
        self.assertContains(response, 'function updateClienteNome()', html=False)

    def test_permite_orcamento_legado_sem_novos_campos(self):
        orcamento = Orcamento.objects.create(codigo='ORC-LEGADO', nome='Legado')

        self.assertEqual(orcamento.codigo_cliente, '')
        self.assertEqual(orcamento.nome_cliente, '')
        self.assertEqual(orcamento.numero_chamado, '')

    def test_importa_orcamentos_de_planilha_xlsx(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A', '12:30', self.pmo_user.username],
                ['1002', '2002', '3002', 'Projeto B', '08:00', self.pmo_user.username],
            ]
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        self.assertTrue(
            Orcamento.objects.filter(
                codigo='1001',
                codigo_cliente='2001',
                numero_chamado='3001',
                nome='Projeto A',
                horas=Decimal('12.50'),
                pmo=self.pmo_user,
                responsavel=self.user,
            ).exists()
        )
        self.assertTrue(Orcamento.objects.filter(codigo='1002').exists())

    def test_importa_horas_formatadas_pelo_excel_como_fracao_de_dia(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A', '0.75', self.pmo_user.username],
                ['1002', '2002', '3002', 'Projeto B', '0.5', self.pmo_user.username],
            ]
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        self.assertEqual(Orcamento.objects.get(codigo='1001').horas, Decimal('18.00'))
        self.assertEqual(Orcamento.objects.get(codigo='1002').horas, Decimal('12.00'))

    def test_importa_horas_acima_de_24_formatadas_como_duracao_excel(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A', {'value': '3.25', 'attrs': {'s': '1', 't': 'n'}}, self.pmo_user.username],
                ['1002', '2002', '3002', 'Projeto B', {'value': '2.5', 'attrs': {'s': '1', 't': 'n'}}, self.pmo_user.username],
            ]
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        self.assertEqual(Orcamento.objects.get(codigo='1001').horas, Decimal('78.00'))
        self.assertEqual(Orcamento.objects.get(codigo='1002').horas, Decimal('60.00'))


    def test_importacao_rejeita_cabecalhos_fora_de_ordem(self):
        response = self.importar_planilha(
            [
                ['Código Cliente', 'Código Orçamento', 'Número do Chamado', 'Descrição'],
                ['2001', '1001', '3001', 'Projeto A'],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'devem estar nesta ordem')
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_importacao_rejeita_orcamento_ja_existente_na_base(self):
        Orcamento.objects.create(codigo='1001', nome='Existente')

        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Duplicado', '12:30', self.pmo_user.username],
                ['1002', '2002', '3002', 'Novo', '08:00', self.pmo_user.username],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1001')
        self.assertContains(response, 'existe na base')
        self.assertFalse(Orcamento.objects.filter(codigo='1002').exists())

    def test_importacao_rejeita_orcamento_duplicado_na_planilha(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A', '12:30', self.pmo_user.username],
                ['1001', '2002', '3002', 'Projeto B', '08:00', self.pmo_user.username],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1001')
        self.assertContains(response, 'duplicado na planilha')
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_importacao_rejeita_campos_numericos_invalidos(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['ORC-1', 'CLI-1', 'CH-1', 'Inválido', '12:30', self.pmo_user.username],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'deve conter somente')
        self.assertEqual(Orcamento.objects.count(), 0)

    def test_importacao_rejeita_quantidade_horas_invalida(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A', '12:75', self.pmo_user.username],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Quantidade de Horas deve estar no formato HH:MM')
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_importacao_rejeita_usuario_nao_marcado_como_pmo(self):
        response = self.importar_planilha(
            [
                self.headers_importacao,
                ['1001', '2001', '3001', 'Projeto A', '12:30', self.non_pmo_user.username],
            ]
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'nao existe ou nao esta marcado como PMO')
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_lista_exibe_opcao_editar_orcamento(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Projeto', responsavel=self.user)

        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, reverse('horas:orcamento_editar', args=[orcamento.pk]))

    def test_formulario_edicao_carrega_dados_do_orcamento(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            codigo_cliente='2001',
            nome_cliente='Cliente Original',
            numero_chamado='3001',
            nome='Projeto original',
            horas='8.50',
            responsavel=self.user,
        )

        response = self.client.get(reverse('horas:orcamento_editar', args=[orcamento.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="1001"', html=False)
        self.assertContains(response, 'value="2001"', html=False)
        self.assertContains(response, 'value="Cliente Original"', html=False)
        self.assertContains(response, 'value="3001"', html=False)
        self.assertContains(response, 'value="Projeto original"', html=False)

    def test_edita_orcamento(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto original',
            horas='8.00',
            responsavel=self.user,
        )

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': '1002',
                'codigo_cliente': '2002',
                'nome_cliente': 'Cliente Atualizado',
                'numero_chamado': '3002',
                'nome': 'Projeto atualizado',
                'horas': '12:45',
                'pmo': self.pmo_user.pk,
            },
        )

        self.assertRedirects(response, reverse('horas:orcamentos'), fetch_redirect_response=False)
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.codigo, '1002')
        self.assertEqual(orcamento.codigo_cliente, '2002')
        self.assertEqual(orcamento.nome_cliente, 'Cliente Atualizado')
        self.assertEqual(orcamento.numero_chamado, '3002')
        self.assertEqual(orcamento.nome, 'Projeto atualizado')
        self.assertEqual(orcamento.horas, Decimal('12.75'))
        self.assertEqual(orcamento.responsavel, self.user)
        self.assertEqual(orcamento.pmo, self.pmo_user)

    def test_nao_permite_reduzir_horas_abaixo_do_total_apontado(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='10.00',
            horas_apontadas='8.00',
            responsavel=self.user,
        )

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': '1001',
                'codigo_cliente': '',
                'numero_chamado': '',
                'nome': 'Projeto',
                'horas': '07:00',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('menor', str(response.context['form'].errors['horas']))
        self.assertEqual(Decimal(str(orcamento.horas)), Decimal('10.00'))

    def test_edicao_rejeita_codigo_duplicado(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Primeiro', responsavel=self.user)
        Orcamento.objects.create(codigo='1002', nome='Segundo')

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': '1002',
                'codigo_cliente': '2001',
                'numero_chamado': '3001',
                'nome': 'Duplicado',
                'horas': '10:00',
            },
        )

        self.assertEqual(response.status_code, 200)
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.codigo, '1001')
        self.assertIn('existe', str(response.context['form'].errors['codigo']))

    def test_edicao_rejeita_letras_nos_campos_numericos(self):
        orcamento = Orcamento.objects.create(codigo='1001', nome='Projeto', responsavel=self.user)

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': 'ORC-1',
                'codigo_cliente': 'CLI-1',
                'numero_chamado': 'CH-1',
                'horas': '10:00',
                'nome': 'Inválido',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'codigo', 'Informe somente números.')
        self.assertIn('escolh', str(response.context['form'].errors['codigo_cliente']))
        self.assertFormError(response.context['form'], 'numero_chamado', 'Informe somente números.')

    def test_outro_usuario_nao_pode_editar_orcamento(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='10.00',
            responsavel=self.other_user,
        )

        response = self.client.get(reverse('horas:orcamento_editar', args=[orcamento.pk]))

        self.assertEqual(response.status_code, 404)

    def test_administrador_pode_editar_orcamento_de_outro_usuario(self):
        admin_user = User.objects.create_user(username='admin-app', password='senha-segura')
        admin_user.profile.is_administrador = True
        admin_user.profile.save(update_fields=['is_administrador'])
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='10.00',
            responsavel=self.other_user,
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse('horas:orcamento_editar', args=[orcamento.pk]),
            data={
                'codigo': '1001',
                'codigo_cliente': '12345',
                'numero_chamado': '3001',
                'nome_cliente': 'Cliente Admin',
                'nome': 'Projeto ajustado',
                'horas': '12:00',
                'pmo': '',
                'ativo': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        orcamento.refresh_from_db()
        self.assertEqual(orcamento.nome, 'Projeto ajustado')
        self.assertEqual(orcamento.responsavel, self.other_user)

    def test_outro_usuario_nao_pode_remover_orcamento(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='10.00',
            responsavel=self.other_user,
        )

        response = self.client.post(reverse('horas:orcamento_remover', args=[orcamento.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Orcamento.objects.filter(pk=orcamento.pk).exists())

    def test_administrador_pode_remover_orcamento_de_outro_usuario(self):
        admin_user = User.objects.create_user(username='admin-remove', password='senha-segura')
        admin_user.profile.is_administrador = True
        admin_user.profile.save(update_fields=['is_administrador'])
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='10.00',
            responsavel=self.other_user,
        )
        self.client.force_login(admin_user)

        response = self.client.post(reverse('horas:orcamento_remover', args=[orcamento.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Orcamento.objects.filter(pk=orcamento.pk).exists())
    def test_lista_oculta_acoes_de_orcamento_de_outro_usuario(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='10.00',
            responsavel=self.other_user,
        )

        response = self.client.get(reverse('horas:orcamentos'))

        self.assertContains(response, self.other_user.username)
        self.assertNotContains(response, reverse('horas:orcamento_editar', args=[orcamento.pk]))
        self.assertNotContains(response, reverse('horas:orcamento_remover', args=[orcamento.pk]))


class OrcamentosPermissionTests(AuthenticatedTestCase):
    def test_usuario_sem_perfil_gp_nao_ve_menu_orcamentos(self):
        response = self.client.get(reverse('horas:timer'))

        self.assertNotContains(response, reverse('horas:orcamentos'))

    def test_usuario_gp_ve_menu_orcamentos(self):
        self.user.profile.is_gerente_projetos = True
        self.user.profile.save(update_fields=['is_gerente_projetos'])

        response = self.client.get(reverse('horas:timer'))

        self.assertContains(response, reverse('horas:orcamentos'))

    def test_usuario_sem_perfil_gp_nao_acessa_orcamentos(self):
        response = self.client.get(reverse('horas:orcamentos'))

        self.assertEqual(response.status_code, 403)

    def test_usuario_sem_perfil_gp_nao_cadastra_orcamento(self):
        response = self.client.post(
            reverse('horas:orcamentos'),
            data={
                'codigo': '1001',
                'codigo_cliente': '2001',
                'numero_chamado': '3001',
                'nome': 'Projeto sem permissao',
                'horas': '20:00',
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Orcamento.objects.filter(codigo='1001').exists())

    def test_usuario_sem_perfil_gp_nao_edita_nem_remove_orcamento(self):
        orcamento = Orcamento.objects.create(
            codigo='1001',
            nome='Projeto',
            horas='20.00',
            responsavel=self.user,
        )

        response_edicao = self.client.get(reverse('horas:orcamento_editar', args=[orcamento.pk]))
        response_remocao = self.client.post(reverse('horas:orcamento_remover', args=[orcamento.pk]))

        self.assertEqual(response_edicao.status_code, 403)
        self.assertEqual(response_remocao.status_code, 403)
        self.assertTrue(Orcamento.objects.filter(pk=orcamento.pk).exists())


class SolicitacoesHorasViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.gp = User.objects.create_user(username='gp-solicitacao', password='senha-segura')
        self.gp.profile.is_gerente_projetos = True
        self.gp.profile.save(update_fields=['is_gerente_projetos'])
        self.outro_gp = User.objects.create_user(username='outro-gp', password='senha-segura')
        self.outro_gp.profile.is_gerente_projetos = True
        self.outro_gp.profile.save(update_fields=['is_gerente_projetos'])
        self.orcamento = Orcamento.objects.create(
            codigo='5001',
            nome='Projeto Solicitação',
            horas='10.00',
            horas_apontadas='10.00',
            responsavel=self.gp,
        )

    def criar_solicitacao(self, *, solicitante=None, orcamento=None, quantidade='5.00', **kwargs):
        return SolicitacaoHoras.objects.create(
            solicitante=solicitante or self.user,
            orcamento=orcamento or self.orcamento,
            quantidade_horas=quantidade,
            motivo='Necessidade de continuidade',
            **kwargs,
        )

    def test_usuario_e_gp_visualizam_menu_solicitar_horas(self):
        response_usuario = self.client.get(reverse('horas:timer'))
        self.client.force_login(self.gp)
        response_gp = self.client.get(reverse('horas:timer'))

        self.assertContains(response_usuario, reverse('horas:solicitacoes_horas'))
        self.assertContains(response_gp, reverse('horas:solicitacoes_horas'))

    def test_apenas_gp_visualiza_menu_e_acessa_pendencias(self):
        response_usuario = self.client.get(reverse('horas:timer'))
        response_negado = self.client.get(reverse('horas:solicitacoes_horas_pendentes'))
        self.client.force_login(self.gp)
        response_gp = self.client.get(reverse('horas:timer'))
        response_permitido = self.client.get(reverse('horas:solicitacoes_horas_pendentes'))

        self.assertNotContains(response_usuario, reverse('horas:solicitacoes_horas_pendentes'))
        self.assertEqual(response_negado.status_code, 403)
        self.assertContains(response_gp, reverse('horas:solicitacoes_horas_pendentes'))
        self.assertEqual(response_permitido.status_code, 200)

    def test_usuario_cria_solicitacao_com_numero_sequencial(self):
        response = self.client.post(
            reverse('horas:solicitacoes_horas'),
            data={
                'orcamento': self.orcamento.pk,
                'quantidade_horas': '05:30',
                'motivo': 'Horas insuficientes para concluir',
            },
        )

        self.assertRedirects(
            response,
            reverse('horas:solicitacoes_horas'),
            fetch_redirect_response=False,
        )
        solicitacao = SolicitacaoHoras.objects.get()
        self.assertEqual(solicitacao.numero_solicitacao, solicitacao.pk)
        self.assertEqual(solicitacao.quantidade_horas, Decimal('5.50'))
        self.assertEqual(solicitacao.situacao, SolicitacaoHoras.SITUACAO_AGUARDANDO)
        self.assertEqual(solicitacao.solicitante, self.user)

    def test_usuario_lista_apenas_suas_solicitacoes(self):
        minha = self.criar_solicitacao()
        outra = self.criar_solicitacao(solicitante=self.other_user)

        response = self.client.get(reverse('horas:solicitacoes_horas'))

        self.assertContains(response, str(minha.pk))
        self.assertNotContains(response, f'<td class="mono accent-text">{outra.pk}</td>', html=False)

    def test_gp_lista_pendencias_apenas_dos_orcamentos_sob_sua_responsabilidade(self):
        minha_pendencia = self.criar_solicitacao()
        outro_orcamento = Orcamento.objects.create(
            codigo='5002',
            horas='10.00',
            responsavel=self.outro_gp,
        )
        outra_pendencia = self.criar_solicitacao(orcamento=outro_orcamento)
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:solicitacoes_horas_pendentes'))

        self.assertContains(response, str(minha_pendencia.pk))
        self.assertNotContains(
            response,
            f'<td class="mono accent-text">{outra_pendencia.pk}</td>',
            html=False,
        )

    def test_menu_gp_exibe_badge_com_quantidade_de_pendencias(self):
        self.criar_solicitacao()
        self.criar_solicitacao(solicitante=self.other_user)
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:timer'))

        self.assertEqual(response.context['pendencias_aprovacao_count'], 2)
        self.assertContains(response, 'has-pending')
        self.assertContains(response, 'class="nav-pending-badge"', html=False)
        self.assertContains(response, '>2</span>', html=False)

    def test_menu_gp_nao_exibe_badge_sem_pendencias(self):
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:timer'))

        self.assertEqual(response.context['pendencias_aprovacao_count'], 0)
        self.assertNotContains(response, 'class="nav-pending-badge"', html=False)
        self.assertNotContains(response, 'has-pending')

    def test_badge_considera_apenas_pendencias_do_gp_logado(self):
        outro_orcamento = Orcamento.objects.create(
            codigo='5002',
            horas='10.00',
            responsavel=self.outro_gp,
        )
        self.criar_solicitacao()
        self.criar_solicitacao(orcamento=outro_orcamento)
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:timer'))

        self.assertEqual(response.context['pendencias_aprovacao_count'], 1)
        self.assertContains(response, '>1</span>', html=False)

    def test_gp_responsavel_aprova_sem_adicionar_horas_ao_orcamento(self):
        solicitacao = self.criar_solicitacao(quantidade='4.50')
        self.client.force_login(self.gp)

        response = self.client.post(
            reverse('horas:solicitacao_horas_decidir', args=[solicitacao.pk]),
            data={'decisao': 'aprovar', 'observacao': 'Aprovado pelo GP'},
        )

        self.assertRedirects(
            response,
            reverse('horas:solicitacoes_horas_pendentes'),
            fetch_redirect_response=False,
        )
        solicitacao.refresh_from_db()
        self.orcamento.refresh_from_db()
        self.assertEqual(solicitacao.situacao, SolicitacaoHoras.SITUACAO_APROVADO)
        self.assertEqual(solicitacao.decidido_por, self.gp)
        self.assertEqual(solicitacao.motivo_reprovacao, 'Aprovado pelo GP')
        self.assertEqual(self.orcamento.horas_adicionais, Decimal('0'))
        self.assertEqual(self.orcamento.horas_disponiveis, Decimal('0'))

        response_processadas = self.client.get(reverse('horas:solicitacoes_horas_pendentes'))
        self.assertContains(response_processadas, 'Aprovado pelo GP')

    def test_gp_nao_pode_aprovar_solicitacao_de_outro_responsavel(self):
        solicitacao = self.criar_solicitacao()
        self.client.force_login(self.outro_gp)

        response = self.client.post(
            reverse('horas:solicitacao_horas_decidir', args=[solicitacao.pk]),
            data={'decisao': 'aprovar'},
        )

        self.assertEqual(response.status_code, 404)
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.situacao, SolicitacaoHoras.SITUACAO_AGUARDANDO)

    def test_reprovacao_exige_motivo_e_exibe_para_solicitante(self):
        solicitacao = self.criar_solicitacao()
        self.client.force_login(self.gp)
        response_sem_motivo = self.client.post(
            reverse('horas:solicitacao_horas_decidir', args=[solicitacao.pk]),
            data={'decisao': 'reprovar', 'observacao': ''},
        )
        solicitacao.refresh_from_db()
        self.assertRedirects(
            response_sem_motivo,
            reverse('horas:solicitacoes_horas_pendentes'),
            fetch_redirect_response=False,
        )
        self.assertEqual(solicitacao.situacao, SolicitacaoHoras.SITUACAO_AGUARDANDO)

        motivo = 'Escopo não aprovado pelo cliente'
        self.client.post(
            reverse('horas:solicitacao_horas_decidir', args=[solicitacao.pk]),
            data={'decisao': 'reprovar', 'observacao': motivo},
        )
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.situacao, SolicitacaoHoras.SITUACAO_REPROVADO)
        self.assertEqual(solicitacao.motivo_reprovacao, motivo)

        self.client.force_login(self.user)
        response_usuario = self.client.get(reverse('horas:solicitacoes_horas'))
        self.assertContains(response_usuario, 'Reprovado')
        self.assertContains(response_usuario, motivo)

        self.client.force_login(self.gp)
        response_processadas = self.client.get(reverse('horas:solicitacoes_horas_pendentes'))
        self.assertContains(response_processadas, 'Observação')
        self.assertContains(response_processadas, motivo)

    def test_solicitacao_aprovada_nao_pode_ser_processada_novamente(self):
        solicitacao = self.criar_solicitacao(quantidade='3.00')
        self.client.force_login(self.gp)
        url = reverse('horas:solicitacao_horas_decidir', args=[solicitacao.pk])

        self.client.post(url, data={'decisao': 'aprovar'})
        self.client.post(url, data={'decisao': 'aprovar'})

        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_adicionais, Decimal('0'))

    def test_horas_aprovadas_nao_ficam_disponiveis_para_apontamento(self):
        solicitacao = self.criar_solicitacao(quantidade='2.00')
        self.client.force_login(self.gp)
        self.client.post(
            reverse('horas:solicitacao_horas_decidir', args=[solicitacao.pk]),
            data={'decisao': 'aprovar'},
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:timer'),
            data={
                'data': date.today().isoformat(),
                'orcamento': self.orcamento.pk,
                'fase': self.fase.pk,
                'servico': self.servico.pk,
                'hora_inicio': '08:00',
                'hora_fim': '10:00',
                'descricao': 'Uso das horas adicionais',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'excede as horas disponíveis')
        self.orcamento.refresh_from_db()
        self.assertEqual(self.orcamento.horas_apontadas, Decimal('10.00'))
        self.assertEqual(self.orcamento.horas_disponiveis, Decimal('0'))


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
    def setUp(self):
        super().setUp()
        self.user.profile.is_gerente_projetos = True
        self.user.profile.save(update_fields=['is_gerente_projetos'])

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

    def test_usuario_comum_nao_ve_menu_fases(self):
        self.user.profile.is_gerente_projetos = False
        self.user.profile.save(update_fields=['is_gerente_projetos'])

        response = self.client.get(reverse('horas:timer'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('horas:fases'))

    def test_usuario_comum_nao_acessa_fases(self):
        self.user.profile.is_gerente_projetos = False
        self.user.profile.save(update_fields=['is_gerente_projetos'])

        response_get = self.client.get(reverse('horas:fases'))
        response_post = self.client.post(
            reverse('horas:fases'),
            data={'codigo': '303', 'descricao': 'Bloqueada'},
        )

        self.assertEqual(response_get.status_code, 403)
        self.assertEqual(response_post.status_code, 403)
        self.assertFalse(Fase.objects.filter(codigo='303').exists())

    def test_usuario_comum_nao_remove_fase(self):
        self.user.profile.is_gerente_projetos = False
        self.user.profile.save(update_fields=['is_gerente_projetos'])
        fase = Fase.objects.create(codigo='399', descricao='Protegida')

        response = self.client.post(reverse('horas:fase_remover', args=[fase.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Fase.objects.filter(pk=fase.pk).exists())


class ServicosViewTests(AuthenticatedTestCase):
    def setUp(self):
        super().setUp()
        self.user.profile.is_gerente_projetos = True
        self.user.profile.save(update_fields=['is_gerente_projetos'])

    def test_lista_servicos_cadastrados(self):
        servico = Servico.objects.create(codigo='S02', descricao='Suporte')

        response = self.client.get(reverse('horas:servicos'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, servico.codigo)
        self.assertContains(response, servico.descricao)

    def test_cadastra_servico_valido(self):
        response = self.client.post(
            reverse('horas:servicos'),
            data={
                'codigo': 'S03',
                'descricao': 'Parametrização',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Servico.objects.filter(codigo='S03', descricao='Parametrização').exists()
        )

    def test_remove_servico(self):
        servico = Servico.objects.create(codigo='S04', descricao='Treinamento')

        response = self.client.post(
            reverse('horas:servico_remover', args=[servico.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Servico.objects.filter(pk=servico.pk).exists())

    def test_usuario_comum_nao_ve_menu_servicos(self):
        self.user.profile.is_gerente_projetos = False
        self.user.profile.save(update_fields=['is_gerente_projetos'])

        response = self.client.get(reverse('horas:timer'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('horas:servicos'))

    def test_usuario_comum_nao_acessa_servicos(self):
        self.user.profile.is_gerente_projetos = False
        self.user.profile.save(update_fields=['is_gerente_projetos'])

        response_get = self.client.get(reverse('horas:servicos'))
        response_post = self.client.post(
            reverse('horas:servicos'),
            data={'codigo': 'S05', 'descricao': 'Bloqueado'},
        )

        self.assertEqual(response_get.status_code, 403)
        self.assertEqual(response_post.status_code, 403)
        self.assertFalse(Servico.objects.filter(codigo='S05').exists())

    def test_usuario_comum_nao_remove_servico(self):
        self.user.profile.is_gerente_projetos = False
        self.user.profile.save(update_fields=['is_gerente_projetos'])
        servico = Servico.objects.create(codigo='S06', descricao='Protegido')

        response = self.client.post(reverse('horas:servico_remover', args=[servico.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Servico.objects.filter(pk=servico.pk).exists())


class UserProfileTests(TestCase):
    def test_cria_profile_para_novo_usuario(self):
        user = User.objects.create_user(username='novo-perfil', password='senha-segura')

        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertEqual(user.profile.codigoerp, 0)
        self.assertFalse(user.profile.is_gerente_projetos)
        self.assertFalse(user.profile.is_administrador)
        self.assertFalse(user.profile.is_pmo)
        self.assertFalse(user.profile.exportacsv)
        self.assertFalse(user.profile.must_change_password)


class AgendaAtividadeModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='agenda-model', password='senha-segura')
        self.gp = User.objects.create_user(username='agenda-gp', password='senha-segura')
        self.gp.profile.is_gerente_projetos = True
        self.gp.profile.save(update_fields=['is_gerente_projetos'])
        self.orcamento = Orcamento.objects.create(codigo='9001', nome='Agenda Orcamento')
        self.servico = Servico.objects.create(codigo='AG01', descricao='Agenda')

    def test_rejeita_data_final_menor_que_inicial(self):
        atividade = AgendaAtividade(
            user=self.user,
            criado_por=self.gp,
            cliente='Cliente',
            numero_chamado='123',
            orcamento=self.orcamento,
            servico=self.servico,
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
            servico=self.servico,
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
            servico=self.servico,
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
            servico=self.servico,
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
            servico=self.servico,
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
            nome_cliente='Cliente Magnus',
            numero_chamado='67890',
            nome='Agenda Orcamento',
        )
        self.servico = Servico.objects.create(codigo='AG02', descricao='Agenda')

    def criar_atividade(self, *, user, criado_por, titulo='Atividade', data_inicio=None, data_fim=None):
        return AgendaAtividade.objects.create(
            user=user,
            criado_por=criado_por,
            cliente='Cliente Agenda',
            numero_chamado='CH-1',
            orcamento=self.orcamento,
            servico=self.servico,
            produto='ERP',
            titulo=titulo,
            descricao='Descricao da atividade',
            data_inicio=data_inicio or date(2026, 6, 10),
            hora_inicio='08:00',
            data_fim=data_fim or date(2026, 6, 12),
            hora_fim='17:00',
            total_horas_maximo=Decimal('16'),
        )

    def test_menu_folgas_feriados_aparece_para_usuario_logado(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:timer'))

        self.assertContains(response, reverse('horas:folgas_feriados'))
        self.assertContains(response, 'Folgas/Feriados')

    def test_usuario_comum_cria_folga_na_propria_agenda(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse('horas:folgas_feriados'), data={'data': '2026-06-15', 'descricao': 'Folga pessoal'})

        self.assertEqual(response.status_code, 302)
        folga = FolgaFeriado.objects.get(descricao='Folga pessoal')
        self.assertEqual(folga.user, self.user)
        self.assertEqual(folga.criado_por, self.user)

    def test_gerente_cria_folga_para_todos_os_usuarios(self):
        self.client.force_login(self.gp)

        response = self.client.post(reverse('horas:folgas_feriados'), data={'data': '2026-06-16', 'descricao': 'Feriado geral', 'aplicar_todos': 'on'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(FolgaFeriado.objects.filter(descricao='Feriado geral').count(), 1)
        folga = FolgaFeriado.objects.get(descricao='Feriado geral')
        self.assertTrue(folga.abrangencia_todos)
        self.assertIsNone(folga.user)
        self.assertEqual(folga.user_nome, 'Todos')

    def test_gerente_edita_folga_para_todos_em_um_unico_registro(self):
        folga = FolgaFeriado.objects.create(
            user=None,
            criado_por=self.gp,
            data=date(2026, 6, 16),
            descricao='Feriado geral',
            abrangencia_todos=True,
        )
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:folgas_feriados'))

        self.assertContains(response, 'Todos')
        self.assertContains(response, reverse('horas:folga_feriado_editar', args=[folga.pk]))

        response = self.client.post(
            reverse('horas:folga_feriado_editar', args=[folga.pk]),
            data={'data': '2026-06-20', 'descricao': 'Feriado alterado'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(FolgaFeriado.objects.count(), 1)
        folga.refresh_from_db()
        self.assertTrue(folga.abrangencia_todos)
        self.assertIsNone(folga.user)
        self.assertEqual(folga.data, date(2026, 6, 20))
        self.assertEqual(folga.descricao, 'Feriado alterado')

        agenda = self.client.get(
            reverse('horas:agenda'),
            {'mes': '2026-06', 'usuario': [self.user.pk, self.other_user.pk]},
        )
        self.assertContains(agenda, 'Feriado alterado', count=2)


    def test_grid_permite_editar_e_remover_apenas_quem_criou_folga(self):
        folga_propria = FolgaFeriado.objects.create(user=self.user, criado_por=self.user, data=date(2026, 6, 17), descricao='Criada por mim')
        folga_gp = FolgaFeriado.objects.create(user=self.user, criado_por=self.gp, data=date(2026, 6, 18), descricao='Criada pelo GP')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:folgas_feriados'))

        self.assertContains(response, reverse('horas:folga_feriado_editar', args=[folga_propria.pk]))
        self.assertContains(response, reverse('horas:folga_feriado_remover', args=[folga_propria.pk]))
        self.assertNotContains(response, reverse('horas:folga_feriado_editar', args=[folga_gp.pk]))
        self.assertNotContains(response, reverse('horas:folga_feriado_remover', args=[folga_gp.pk]))
        response = self.client.get(reverse('horas:folga_feriado_editar', args=[folga_gp.pk]))
        self.assertEqual(response.status_code, 404)

    def test_remove_folga_feriado_criada_pelo_usuario(self):
        folga = FolgaFeriado.objects.create(user=self.user, criado_por=self.user, data=date(2026, 6, 17), descricao='Remover folga')
        self.client.force_login(self.user)

        response = self.client.post(reverse('horas:folga_feriado_remover', args=[folga.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(FolgaFeriado.objects.filter(pk=folga.pk).exists())

    def test_nao_remove_folga_feriado_criada_por_outro_usuario_exibe_mensagem(self):
        folga = FolgaFeriado.objects.create(user=self.user, criado_por=self.gp, data=date(2026, 6, 18), descricao='Folga do GP')
        self.client.force_login(self.user)

        response = self.client.post(reverse('horas:folga_feriado_remover', args=[folga.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(FolgaFeriado.objects.filter(pk=folga.pk).exists())
        self.assertContains(response, 'Só é possível excluir registros criados pelo próprio usuário.')

    def test_agenda_exibe_folga_feriado_em_card_amarelo(self):
        FolgaFeriado.objects.create(user=self.user, criado_por=self.user, data=date(2026, 6, 10), descricao='Descanso')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, 'agenda-event-holiday')
        self.assertContains(response, 'Folga/Feriado')
        self.assertContains(response, 'Descanso')

    def test_agenda_comparativa_exibe_folgas_por_usuario(self):
        FolgaFeriado.objects.create(user=self.user, criado_por=self.gp, data=date(2026, 6, 10), descricao='Folga usuario')
        FolgaFeriado.objects.create(user=self.other_user, criado_por=self.gp, data=date(2026, 6, 10), descricao='Folga outro')
        self.client.force_login(self.gp)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06', 'usuario': [self.user.pk, self.other_user.pk]})

        self.assertContains(response, 'Folga usuario')
        self.assertContains(response, 'Folga outro')
        day = next(
            day
            for week in response.context['agenda_weeks']
            for day in week
            if day['date'] == date(2026, 6, 10)
        )
        grupos = {group['user'].pk: [folga.descricao for folga in group['folgas_feriados']] for group in day['user_groups']}
        self.assertEqual(grupos[self.user.pk], ['Folga usuario'])
        self.assertEqual(grupos[self.other_user.pk], ['Folga outro'])

    def test_cria_atividade_em_data_com_folga_feriado(self):
        FolgaFeriado.objects.create(user=self.user, criado_por=self.user, data=date(2026, 6, 10), descricao='Folga pessoal')
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'numero_chamado': 'CH-200',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
                'produto': 'ERP',
                'titulo': 'Atividade em folga',
                'descricao': 'Descricao',
                'data_inicio': '2026-06-10',
                'hora_inicio': '09:00',
                'data_fim': '2026-06-10',
                'hora_fim': '18:00',
                'total_horas_maximo': '08:00',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(AgendaAtividade.objects.filter(titulo='Atividade em folga').exists())

    def test_gerente_cria_atividade_em_intervalo_com_folga_feriado_do_usuario(self):
        FolgaFeriado.objects.create(user=self.user, criado_por=self.gp, data=date(2026, 6, 11), descricao='Folga usuario')
        self.client.force_login(self.gp)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'user': self.user.pk,
                'cliente': 'Cliente Agenda',
                'numero_chamado': 'CH-100',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
                'produto': 'ERP',
                'titulo': 'Atividade delegada em folga',
                'descricao': 'Descricao',
                'data_inicio': '2026-06-10',
                'hora_inicio': '08:30',
                'data_fim': '2026-06-12',
                'hora_fim': '17:30',
                'total_horas_maximo': '20:30',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(AgendaAtividade.objects.filter(titulo='Atividade delegada em folga').exists())

    def test_agenda_renderiza_semanas_retrateis(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        total_weeks = len(response.context['agenda_weeks'])
        content = response.content.decode()
        self.assertContains(response, 'data-agenda-weeks', html=False)
        self.assertEqual(content.count('data-agenda-week-toggle\n'), total_weeks)
        self.assertEqual(content.count('data-agenda-week-panel hidden'), total_weeks)
        self.assertContains(response, '.agenda-week-panel[hidden] { display: none !important; }', html=False)
        self.assertContains(response, 'setWeekExpanded', html=False)
        self.assertContains(response, 'setWeekExpanded(week, !panel || panel.hidden)', html=False)
        self.assertContains(response, 'Semana 1', html=False)

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

    def test_gerente_filtra_multiplos_usuarios_e_ve_agendas_agrupadas(self):
        self.criar_atividade(user=self.user, criado_por=self.gp, titulo='Planejamento usuario')
        self.criar_atividade(user=self.other_user, criado_por=self.gp, titulo='Planejamento outro')
        self.client.force_login(self.gp)

        response = self.client.get(
            reverse('horas:agenda'),
            {'mes': '2026-06', 'usuario': [self.user.pk, self.other_user.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['agenda_compare_mode'])
        self.assertEqual(
            {selected_user.pk for selected_user in response.context['selected_users']},
            {self.user.pk, self.other_user.pk},
        )
        atividades = list(response.context['agenda_atividades'])
        self.assertEqual({atividade.titulo for atividade in atividades}, {'Planejamento usuario', 'Planejamento outro'})

        day = next(
            day
            for week in response.context['agenda_weeks']
            for day in week
            if day['date'] == date(2026, 6, 10)
        )
        grupos = {group['user'].pk: [atividade.titulo for atividade in group['atividades']] for group in day['user_groups']}
        self.assertEqual(grupos[self.user.pk], ['Planejamento usuario'])
        self.assertEqual(grupos[self.other_user.pk], ['Planejamento outro'])
        self.assertContains(response, self.user.username)
        self.assertContains(response, self.other_user.username)
        self.assertContains(response, 'data-user-picker')
        self.assertNotContains(response, 'type="checkbox"')

    def test_usuario_comum_ignora_filtro_multiusuario(self):
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Minha atividade')
        self.criar_atividade(user=self.other_user, criado_por=self.other_user, titulo='Atividade de outro')
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('horas:agenda'),
            {'mes': '2026-06', 'usuario': [self.user.pk, self.other_user.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['agenda_compare_mode'])
        atividades = list(response.context['agenda_atividades'])
        self.assertEqual(len(atividades), 1)
        self.assertEqual(atividades[0].titulo, 'Minha atividade')

    def test_filtro_multiusuario_mantem_usuarios_na_navegacao(self):
        self.client.force_login(self.gp)

        response = self.client.get(
            reverse('horas:agenda'),
            {'mes': '2026-06', 'usuario': [self.user.pk, self.other_user.pk]},
        )

        self.assertContains(response, 'mes=2026-05')
        self.assertContains(response, f'usuario={self.user.pk}')
        self.assertContains(response, f'usuario={self.other_user.pk}')
        self.assertContains(response, 'mes=2026-07')

    def test_nova_atividade_na_comparacao_nao_preseleciona_usuario(self):
        self.client.force_login(self.gp)

        response = self.client.get(
            reverse('horas:agenda'),
            {'mes': '2026-06', 'usuario': [self.user.pk, self.other_user.pk]},
        )

        self.assertContains(response, f'{reverse("horas:agenda_nova")}?mes=2026-06')
        self.assertNotContains(response, f'{reverse("horas:agenda_nova")}?mes=2026-06&amp;usuario=')
        self.assertContains(response, f'{reverse("horas:agenda_nova")}?data=2026-06-10&mes=2026-06')

    def test_gerente_cria_atividade_para_outro_usuario(self):
        self.client.force_login(self.gp)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'user': self.user.pk,
                'cliente': 'Cliente Agenda',
                'numero_chamado': 'CH-100',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
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
                'servico': self.servico.pk,
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

    def test_usuario_comum_cria_atividade_terceiro_com_quantidade_de_horas(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'numero_chamado': 'CH-200',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
                'produto': 'ERP',
                'destino_para': AgendaAtividade.DESTINO_TERCEIRO,
                'titulo': 'Atividade terceiro',
                'descricao': 'Descricao',
                'data_inicio': '2026-06-10',
                'data_fim': '2026-06-11',
                'quantidade_horas': '06:30',
                'total_horas_maximo': '06:30',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade = AgendaAtividade.objects.get(titulo='Atividade terceiro')
        self.assertEqual(atividade.destino_para, AgendaAtividade.DESTINO_TERCEIRO)
        self.assertIsNone(atividade.hora_inicio)
        self.assertIsNone(atividade.hora_fim)
        self.assertEqual(atividade.quantidade_horas, Decimal('6.5'))
        self.assertEqual(atividade.total_horas_maximo, Decimal('6.5'))

    def test_total_horas_maximo_aceita_digitos_sem_separador(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
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
        self.assertContains(response, 'name="destino_para"', html=False)
        self.assertContains(response, 'name="quantidade_horas"', html=False)
        self.assertContains(response, 'data-schedule-field="terceiro"', html=False)
        self.assertContains(response, 'maskDurationInput', html=False)
        self.assertContains(response, 'suggestMaximumHoursFromSchedule', html=False)
        self.assertContains(response, 'formatMinutesAsDuration(endMinutes - startMinutes)', html=False)
        self.assertContains(response, 'suggestMaximumHoursFromQuantity', html=False)

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
                'servico': self.servico.pk,
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

    def test_formulario_produto_exibe_opcoes_disponiveis(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda_nova'))
        produto = response.context['form'].fields['produto']

        self.assertEqual(list(produto.choices), [('', '— selecione —'), ('ERP', 'ERP'), ('HCM', 'HCM'), ('PMO', 'PMO'), ('GAS', 'GAS')])
        self.assertTrue(produto.required)

    def test_rejeita_produto_fora_da_lista(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
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
                'servico': atividade.servico.pk,
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

    def test_administrador_pode_editar_atividade_de_qualquer_usuario(self):
        admin_user = User.objects.create_user(username='admin-agenda', password='senha-segura')
        admin_user.profile.is_administrador = True
        admin_user.profile.save(update_fields=['is_administrador'])
        atividade = self.criar_atividade(user=self.other_user, criado_por=self.gp, titulo='Atividade externa')
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse('horas:agenda_editar', args=[atividade.pk]),
            data={
                'user': self.other_user.pk,
                'cliente': atividade.cliente,
                'numero_chamado': atividade.numero_chamado,
                'orcamento': atividade.orcamento.pk,
                'servico': atividade.servico.pk,
                'produto': atividade.produto,
                'titulo': 'Atividade administrada',
                'descricao': atividade.descricao,
                'data_inicio': atividade.data_inicio.isoformat(),
                'hora_inicio': '11:00',
                'data_fim': atividade.data_fim.isoformat(),
                'hora_fim': '18:00',
                'total_horas_maximo': '07:00',
            },
        )

        self.assertEqual(response.status_code, 302)
        atividade.refresh_from_db()
        self.assertEqual(atividade.titulo, 'Atividade administrada')
        self.assertEqual(atividade.user, self.other_user)
    def test_rejeita_nova_atividade_sem_horarios_e_total_maximo(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('horas:agenda_nova'),
            data={
                'cliente': 'Cliente Agenda',
                'orcamento': self.orcamento.pk,
                'servico': self.servico.pk,
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

    def test_calendario_exibe_horarios_e_total_maximo_no_modal(self):
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Com planejamento')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, '08:00 - 17:00')
        self.assertContains(response, 'data-max-hours="16:00"', html=False)
        self.assertNotContains(response, 'Máx. 16:00')

    def test_card_agenda_exibe_somente_cliente_e_horario(self):
        self.orcamento.horas = Decimal('20')
        self.orcamento.horas_apontadas = Decimal('7.5')
        self.orcamento.save(update_fields=['horas', 'horas_apontadas'])
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Saldo atualizado')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, '12345 - Cliente Magnus')
        self.assertContains(response, '08:00 - 17:00')
        self.assertNotContains(response, 'HRS APT:')
        self.assertNotContains(response, 'HRS DIS:')
        self.assertNotContains(response, 'Máx.')

    def test_card_agenda_exibe_sem_nome_cliente_quando_orcamento_nao_tem_nome_cliente(self):
        self.orcamento.nome_cliente = ''
        self.orcamento.save(update_fields=['nome_cliente'])
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Sem nome cliente')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, '12345 - Sem Nome Cliente')
        self.assertContains(response, 'data-client="12345 - Sem Nome Cliente"', html=False)

    def test_card_agenda_nao_exibe_pmo_do_orcamento(self):
        pmo = User.objects.create_user(
            username='pmo-agenda',
            password='senha-segura',
            first_name='Patricia',
            last_name='PMO',
        )
        self.orcamento.pmo = pmo
        self.orcamento.save(update_fields=['pmo'])
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Com PMO')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertNotContains(response, 'PMO: Patricia PMO')

    def test_card_agenda_fica_vermelho_quando_nao_ha_horas_disponiveis(self):
        self.orcamento.horas = Decimal('8')
        self.orcamento.horas_adicionais = Decimal('2')
        self.orcamento.horas_apontadas = Decimal('10')
        self.orcamento.save(update_fields=['horas', 'horas_adicionais', 'horas_apontadas'])
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Sem saldo')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, 'is-out-of-hours')
        self.assertNotContains(response, 'HRS DIS:')
        self.assertContains(response, '.agenda-event.is-out-of-hours')

    def test_card_agenda_mantem_cor_normal_quando_ha_horas_disponiveis(self):
        self.orcamento.horas = Decimal('10')
        self.orcamento.horas_apontadas = Decimal('9')
        self.orcamento.save(update_fields=['horas', 'horas_apontadas'])
        self.criar_atividade(user=self.user, criado_por=self.user, titulo='Com saldo')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})
        card = response.content.decode().split('Com saldo', 1)[0].rsplit('<div class="agenda-event', 1)[-1]

        self.assertNotIn('is-out-of-hours', card.split('">', 1)[0])
        self.assertNotContains(response, 'HRS DIS:')

    def test_card_agenda_disponibiliza_apontar_no_modal_com_data_e_orcamento(self):
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
                f'{reverse("horas:timer")}?data={data_card}&orcamento={atividade.orcamento_id}&servico={atividade.servico_id}',
            )
        self.assertContains(response, '>Apontar</a>', count=1, html=False)

    def test_card_agenda_disponibiliza_editar_no_modal_quando_usuario_pode_gerenciar(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.user, titulo='Editável')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertContains(response, reverse('horas:agenda_editar', args=[atividade.pk]))
        self.assertContains(response, 'data-can-manage="true"', html=False)
        self.assertContains(response, '>Editar</a>', count=1, html=False)

    def test_card_agenda_nao_exibe_botao_editar_sem_permissao(self):
        atividade = self.criar_atividade(user=self.user, criado_por=self.gp, titulo='Delegada')
        self.client.force_login(self.user)

        response = self.client.get(reverse('horas:agenda'), {'mes': '2026-06'})

        self.assertNotContains(response, reverse('horas:agenda_editar', args=[atividade.pk]))
        self.assertContains(response, 'data-can-manage="false"', html=False)

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

        self.assertContains(response, f'mes=2026-05&amp;usuario={self.user.pk}')
        self.assertContains(response, f'mes=2026-07&amp;usuario={self.user.pk}')

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

