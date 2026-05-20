# Exportador Blender `.bsc` / `.bon`

Este repositório contém um script de Add-on para Blender (3.x/4.x) que exporta objetos selecionados da cena para um formato binário proprietário:

- `.bsc` → malha/chunks/texturas
- `.bon` → armature/ossos/pesos

Arquivo principal do add-on:

- `blender_bsc_bon_exporter.py`

---

## 1) Requisitos

- Blender 3.x ou 4.x
- Cena com objetos `MESH` selecionados
- (Opcional) Objeto `ARMATURE` selecionado para gerar `.bon`

---

## 2) Instalação do Add-on no Blender

1. Abra o Blender.
2. Vá em **Edit > Preferences > Add-ons**.
3. Clique em **Install...**.
4. Selecione o arquivo `blender_bsc_bon_exporter.py`.
5. Ative o add-on **BSC/BON Proprietary Exporter** na lista.

> Dica: você também pode copiar o script para a pasta de add-ons do Blender e ativá-lo por lá.

---

## 3) Como usar (passo a passo)

### Método A: Pelo menu de Exportação

1. Selecione na cena os objetos que deseja exportar.
   - O script considera objetos do tipo **MESH**.
   - Se houver **ARMATURE** selecionada, ela será usada para o `.bon`.
2. Vá em **File > Export > Proprietary BSC/BON (.bsc)**.
3. Escolha o caminho e nome do arquivo `.bsc`.
4. No painel da janela de exportação:
   - mantenha **Export .bon** habilitado para gerar também o `.bon`.
   - mantenha **Create sidecars (.ba0/.bb0/.bc0/.bd0/.bao/.bbo)** habilitado para criar sidecars obrigatórios (placeholders).
5. Clique em **Export BSC/BON**.

> Observação: os sidecars (`.ba0/.bb0/.bc0/.bd0/.bao/.bbo`) agora são gravados como **DDS mascarado** (conteúdo começando com `DDS `).

### Método B: Pelo painel lateral da View 3D

1. Abra a **View3D**.
2. Pressione `N` para abrir a barra lateral.
3. Vá até a aba **BSC/BON**.
4. Clique em **Export BSC/BON**.
5. Defina o caminho do `.bsc` e confirme.

---

## 4) O que o script exporta

### `.bsc`

- Cabeçalho inicial com 3 bytes:
  - Byte `0x00`: quantidade de chunks
  - Byte `0x01`: quantidade de texturas
  - Byte `0x02`: byte de controle/flag (atual: `0x00`)
- Blocos de 12 bytes por chunk (layout atual implementado como base)
- Dados de vértices (posição, normal, UV)
- Stream de índices (placeholder atual em `UInt16`)

### `.bon`

- Cabeçalho com contagens de ossos/pesos/chunks
- Tabela de ossos:
  - nome fixo (32 bytes)
  - índice do pai
  - matriz local 4x4
- Entradas de peso:
  - chunk alvo
  - índice do vértice
  - osso influenciador
  - valor do peso

---

## 5) Convenção de prefixos para flags

O exportador analisa prefixos no nome do objeto para setar flags especiais no `.bsc`.

Mapa atual:

- `COL_...` → `0x01`
- `DMG_...` → `0x02`
- `ENV_...` → `0x04`
- `LOD_...` → `0x08`

Se o objeto não corresponder ao mapa, a flag fica `0x00`.

---

## 6) Exemplo rápido

Suponha que você tenha:

- `COL_Body` (mesh)
- `DMG_Head` (mesh)
- `Armature` (armature)

Selecione os 3 objetos e exporte para:

- `meu_modelo.bsc`

Com **Export .bon** ligado, o Blender criará:

- `meu_modelo.bsc`
- `meu_modelo.bon`
- `meu_modelo.ba0`, `meu_modelo.bb0`, `meu_modelo.bc0`, `meu_modelo.bd0` (placeholders)
- `meu_modelo.bao`, `meu_modelo.bbo` (variante usada por algumas builds)

Esses sidecars são exportados como DDS mascarado para compatibilidade com o loader.

---

## 7) Limitações atuais (importante)

- Atualização recente: o exportador agora gera **1 chunk por objeto** (em vez de 1 por polígono) e índices de malha em `UInt32`, reduzindo casos de modelo invisível por layout inválido.
Como o formato é proprietário e ainda pode estar em engenharia reversa:

- O layout de 12 bytes por chunk está implementado como **estrutura base funcional**.
- Há **placeholders comentados** no código para ajustar:
  - tabelas extras de material/textura
  - organização final de buffers de vértices/índices
  - blocos extras de armature/anim
- Dependendo do jogo/engine, você pode precisar adaptar offsets, tipos numéricos e ordem dos blocos.

---

## 8) Dicas de validação

1. Exporte um modelo simples (1 mesh, 1 material, poucos triângulos).
2. Compare em hex editor com um arquivo original do jogo.
3. Ajuste os `struct.pack` conforme diferenças encontradas.
4. Evolua para modelos com múltiplos chunks, materiais e ossos.

---

## 9) Solução de problemas

- **Nada exporta**:
  - verifique se há objetos `MESH` selecionados.
- **`.bon` vazio**:
  - selecione também uma `ARMATURE` válida.
- **Texturas não detectadas**:
  - o script busca nós `TEX_IMAGE` em materiais com nodes.
- **Erro de arquivo ausente no jogo**:
  - habilite a opção **Create sidecars (.ba0/.bb0/.bc0/.bd0/.bao/.bbo)** ao exportar.
  - algumas builds procuram `.ba0` e outras usam `.bao` (o script agora gera ambas).
- **Arquivo rejeitado pela engine**:
  - revise os placeholders e ajuste o layout binário para o padrão exato esperado.

---

## 10) Próximos passos sugeridos

- Adicionar opção de presets de layout por versão do jogo.
- Implementar logs de exportação mais detalhados.
- Criar importador de teste para round-trip dentro do Blender.
