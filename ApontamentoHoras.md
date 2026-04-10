# Plano de Aplicativo - Controle de Apontamento de Horas

## Visão Geral
Aplicativo web para controle de apontamento de horas desenvolvido em Django com banco de dados SQLite. Interface idêntica ao arquivo `apontamento-horas.html` existente.

## Tecnologias
- **Backend**: Python 3.10+ com Django 4.2+
- **Frontend**: Django Templates (sem JavaScript adicional)
- **Banco de Dados**: SQLite
- **Estilos**: CSS inline nos templates (copiados do HTML existente)
- **Ícones**: Unicode (como no HTML original)

## Estrutura do Projeto

```
magnusRotinas_django/
├── manage.py
├── magnusRotinas/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── horas/
│   ├── __init__.py
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── views.py
│   ├── urls.py
│   ├── tests.py
│   └── migrations/
├── templates/
│   └── base.html
└── static/
    └── css/
        └── style.css
```

## Models

### 1. Orcamento (Orçamentos)
```python
class Orcamento(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nome = models.CharField(max_length=200, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.codigo} - {self.nome}"
```

### 2. Registro (Registros de Horas)
```python
class Registro(models.Model):
    orcamento = models.ForeignKey(Orcamento, on_delete=models.PROTECT, related_name='registros')
    data = models.DateField()
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()
    descricao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    
    @property
    def total_horas(self):
        inicio = datetime.combine(datetime.min, self.hora_inicio)
        fim = datetime.combine(datetime.min, self.hora_fim)
        delta = fim - inicio
        if delta.days < 0:
            delta += timedelta(days=1)
        return delta.total_seconds() / 3600
    
    def __str__(self):
        return f"{self.data} - {self.hora_inicio} às {self.hora_fim}"
```

## Views

### 1. TimerView
- **URL**: `/timer/`
- **Template**: `timer.html`
- **Funcionalidades**:
  - Formulário para novo apontamento
  - Timer (usando JavaScript no template)
  - Preenchimento automático de data/hora
  - Salvar manual

### 2. RegistrosView
- **URL**: `/registros/`
- **Template**: `registros.html`
- **Funcionalidades**:
  - Lista de registros
  - Filtro por período (data inicial/data final)
  - Filtro por orçamento
  - Exportar CSV

### 3. ResumoView
- **URL**: `/resumo/`
- **Template**: `resumo.html`
- **Funcionalidades**:
  - Estatísticas totais
  - Total de horas
  - Dias trabalhados
  - Média diária
  - Detalhamento por orçamento

### 4. OrcamentosView
- **URL**: `/orcamentos/`
- **Template**: `orcamentos.html`
- **Funcionalidades**:
  - Listar orçamentos
  - Adicionar novo orçamento
  - Remover orçamento

### 5. DashboardView (opcional)
- **URL**: `/`
- **Template**: `dashboard.html`
- Funcionalidade: Redirecionar para timer

## Templates

### 1. base.html
- Estrutura base com sidebar e navegação
- Inclui todos os estilos CSS
- Template para todas as páginas

### 2. timer.html
- Formulário de apontamento
- Timer com display
- Campos: data, orçamento, hora início, hora fim, descrição
- Botões: Iniciar/Parar, Salvar manual

### 3. registros.html
- Tabela com registros
- Filtros: período, orçamento
- Botão exportar CSV
- Botão remover registro

### 4. resumo.html
- Grid com estatísticas
- Filtros: período
- Tabela com detalhamento por orçamento

### 5. orcamentos.html
- Grid com cards de orçamentos
- Formulário para adicionar novo
- Botão remover orçamento

## URLs

```python
# urls.py do projeto
urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('timer/', views.TimerView.as_view(), name='timer'),
    path('registros/', views.RegistrosView.as_view(), name='registros'),
    path('resumo/', views.ResumoView.as_view(), name='resumo'),
    path('orcamentos/', views.OrcamentosView.as_view(), name='orcamentos'),
]
```

## Funcionalidades Específicas

### 1. Timer
- Usar JavaScript no template para funcionalidade do timer
- Salvar automaticamente ao parar
- Preencher data e hora início automaticamente

### 2. Export CSV
- Gerar CSV com BOM UTF-8
- Separador ponto e vírgula (;)
- Campos: Data, Hora Inicio, Hora Fim, Total, Código Orçamento, Descrição

### 3. Filtros
- Filtros por período em registros e resumo
- Filtro por orçamento
- Validação de datas (início ≤ fim)

### 4. Validações
- Horas início < horas fim
- Data não futura
- Orçamento ativo para registros

## Migração de Dados (se necessário)
- Script para migrar dados do localStorage para o banco de dados
- Manter estrutura equivalente

## Instruções de Execução

1. Criar projeto Django:
```bash
django-admin startproject magnusRotinas
cd magnusRotinas
python manage.py startapp horas
```

2. Configurar settings.py:
- Adicionar 'horas' a INSTALLED_APPS
- Configurar templates e static

3. Criar models e executar migrações

4. Criar views e templates

5. Configurar URLs

6. Executar servidor de desenvolvimento:
```bash
python manage.py runserver
```

## Considerações
- Interface exatamente igual ao HTML fornecido
- Manter o mesmo esquema de cores e fontes
- Navegação entre páginas sem recarregar completo
- Mensagens de feedback (toast) usando Django Messages