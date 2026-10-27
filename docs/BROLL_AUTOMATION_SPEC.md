# Especificação inicial: automação de B-roll no CapCut

**Status:** especificação inicial consolidada; implementação da primeira integração em andamento
**Objetivo:** orientar a ferramenta e a skill que prepararão alternativas de B-roll no projeto CapCut existente.

## 1. Objetivo

Criar uma ferramenta que uma IA acione por meio de uma skill futura. O usuário prepara a copy, gera e trata o áudio, cria o projeto do CapCut com a nomenclatura desejada e posiciona o áudio. Em seguida, aciona a skill no chat para que a IA analise a copy e monte opções de B-roll no projeto existente.

A ferramenta deve acelerar a montagem sem inserir imagens aleatórias ou vídeos de banco. O usuário revisa as opções no CapCut e decide quais takes manter.

## 2. Entradas esperadas

- Projeto do CapCut já criado e salvo, com o áudio tratado na linha do tempo.
- Copy completa correspondente ao áudio.
- Identificação inequívoca do projeto pelo nome, conforme exibido no CapCut.
- A copy e o nome do projeto serão enviados no chat depois que o usuário acionar a skill.
- Chave da API guardada localmente, fora do código, do Git e das mensagens do chat.

O usuário faz previamente a geração e o tratamento do áudio. A ferramenta não deve refazer essa etapa.

## 3. Fluxo esperado

1. A IA lê a copy completa antes de editar.
2. A IA interpreta o contexto e marca somente os trechos em que um apoio visual de B-roll melhora a compreensão ou a força da mensagem. Cada take precisa representar o que está sendo dito: se a copy citar uma pessoa específica, buscar essa pessoa; se mencionar uma etnia, buscar uma pessoa dessa etnia.
3. Para cada trecho escolhido, a IA localiza o ponto correspondente no áudio que já está na linha do tempo do projeto. Na primeira versão, usar a copy enviada e uma transcrição local com timestamps, alinhada à copy, para obter os tempos.
4. A IA busca takes orgânicos que correspondam ao significado do trecho. Pode usar vídeos de qualquer país; o idioma e o país não são filtros obrigatórios quando o conteúdo visual corresponde à fala.
5. A IA baixa **três alternativas** para cada ponto de B-roll, quando houver três opções adequadas e autorizadas.
6. Cada alternativa usa os **primeiros cinco segundos** do vídeo de origem e é inserida no ponto correspondente da linha do tempo.
7. Existem exatamente três faixas fixas de B-roll acima do áudio: `broll_option_1`, `broll_option_2` e `broll_option_3`. A opção 1 de cada ponto reutiliza sempre a primeira faixa, a opção 2 a segunda e a opção 3 a terceira. As três opções de um ponto ficam sobrepostas no tempo, empilhadas verticalmente; pontos seguintes reutilizam as mesmas faixas, sem criar faixas novas.
8. A IA verifica a cobertura visual de toda a timeline: nenhum intervalo sem B-roll pode exceder **sete segundos**.
9. A IA envia atualizações periódicas no chat, incluindo a contagem de takes posicionados. Quando a cobertura única da timeline atingir um terço da duração do áudio, deve avisar explicitamente.
10. Ao concluir, a IA informa que terminou e resume quantos pontos foram encontrados, quantos candidatos foram baixados e quantos foram posicionados.

## 4. Regras editoriais

- Não inserir B-roll aleatoriamente nem em cada frase da copy.
- Cada take precisa ter relação visual clara com o trecho específico da copy e do áudio.
- Usar vídeos orgânicos de redes sociais; não usar vídeos de banco de imagens.
- Cada ponto deve ter três takes candidatos para o usuário comparar. As opções começam no mesmo ponto da fala e ficam empilhadas verticalmente nas três faixas fixas `broll_option_1..3`; cada faixa é reutilizada nos pontos seguintes.
- Usar os primeiros cinco segundos de cada vídeo de origem. Não inserir o vídeo inteiro; o usuário poderá expandir o trecho depois de revisar a opção.
- A busca pode retornar conteúdo de qualquer país. Relevância visual para a fala tem prioridade sobre o país ou o idioma do vídeo.
- Para pessoa pública ou nomeada na copy (por exemplo, Donald Trump), buscar imagens/vídeos dessa pessoa. Para referência explícita a uma etnia, buscar alguém dessa etnia; não inferir a etnia apenas pela aparência quando a fonte não a identifica.
- O intervalo entre trechos com B-roll visível não pode passar de sete segundos. Verificar também o começo da timeline até o primeiro B-roll e o último B-roll até o fim do anúncio.
- Planejar os pontos de B-roll para respeitar esse limite sem perder a relação semântica com a copy. A regra de sete segundos não autoriza inserir takes aleatórios; se não houver material relevante e autorizado para cobrir um intervalo, reportar a lacuna em vez de usar um vídeo sem relação.
- Manter o áudio original da copy intacto.
- Por padrão, silenciar o áudio original dos B-rolls para não competir com a narração.
- Não exportar o anúncio; a tarefa termina com as opções posicionadas no projeto para revisão humana.

## 5. Projeto e integração com CapCut

- A ferramenta deve editar **o projeto existente no próprio local** (modo `in_place`).
- Não criar outra cópia do projeto como fluxo normal de produção.
- Não alterar o áudio tratado nem partes da timeline que não façam parte da tarefa.
- Salvar os arquivos de mídia necessários dentro da estrutura do projeto e registrar os metadados exigidos pelo CapCut.
- Antes de gravar as alterações por MCP, o CapCut deve estar fechado para evitar que o autosave da sessão aberta sobrescreva as mudanças. O usuário poderá reabrir o projeto para revisar.
- Preservar backups reversíveis do conteúdo do projeto antes de salvar alterações.

## 6. Busca e plataformas

### Primeira plataforma

- TikTok via FetchLayer é a primeira integração de busca e download.
- O fluxo TikTok → MP4 → inserção no projeto existente em uma posição específica foi testado com sucesso.
- A chave FetchLayer deve ser lida localmente e nunca exibida em logs, respostas, arquivos rastreados pelo Git ou mensagens.

### Plataformas futuras

Cada integração deve ser validada individualmente. Uma mesma chave não garante que cada plataforma ofereça busca por palavras-chave e download de vídeo.

- Instagram: a documentação atual oferece leitura de Reels/posts e download de mídia, mas não busca geral por palavra-chave; o modo de descoberta precisa ser definido.
- YouTube: os endpoints FetchLayer verificados cobrem comentários e transcrições; não oferecem download de vídeo.
- Reddit: os endpoints cobrem busca e dados de posts; a disponibilidade de uma URL de mídia não equivale a um fluxo de download MP4 validado.
- Pinterest: não está disponível na chave/API FetchLayer que foi verificada até agora; exigirá outra integração.

Não considerar uma plataforma pronta até que a busca relevante, obtenção do arquivo, importação no projeto e reprodução no CapCut tenham sido testadas.

## 7. Direitos e procedência

Como o material será usado em anúncios, só baixar e inserir conteúdo que a empresa tenha autorização para reutilizar comercialmente. Registrar, junto a cada candidato, a plataforma, URL de origem, identificador/autor disponível, arquivo local e ponto de inserção.

- O usuário informa que a empresa tem permissão para baixar qualquer vídeo do TikTok. A ferramenta pode usar essa autorização declarada no fluxo TikTok e deve registrar a origem de cada candidato.
- Essa autorização declarada é específica ao TikTok. Outras plataformas precisam de integração e autorização próprias.
- Como o destino é anúncio, a empresa continua responsável por garantir que a autorização existente cubra o uso comercial e a imagem/voz das pessoas exibidas.

## 8. Progresso, velocidade e falhas

- Priorizar rapidez com chamadas concorrentes limitadas, sem perder a correspondência entre candidato e ponto da copy.
- Cada busca de página e cada download de mídia pode consumir créditos FetchLayer. Uma página de busca e três downloads representam cerca de quatro créditos por ponto; cache de resultados/arquivos deve evitar cobranças repetidas quando possível. Informar o saldo estimado antes de uma execução que não possa ser completada com o saldo disponível.
- Mostrar progresso no chat em termos compreensíveis, por exemplo: `2 de 5 pontos analisados; 6 de 15 takes posicionados`.
- Calcular a cobertura como tempo único da timeline coberto por B-roll; os três candidatos sobrepostos no mesmo ponto contam como um único intervalo coberto, não como 15 segundos de cobertura.
- Enviar um aviso destacado quando essa cobertura única alcançar pelo menos um terço da duração total do áudio.
- Não substituir uma busca sem resultado por um take sem relação só para completar três opções.
- Se não houver três candidatos válidos, posicionar apenas os válidos e informar a quantidade faltante e o motivo.
- Se download, leitura de mídia, gravação ou validação falhar, informar o ponto afetado e não alegar conclusão completa.
- Ao final, verificar que cada arquivo existe, que os metadados estão registrados e que os segmentos começam nos tempos planejados e têm cinco segundos.

## 9. Critérios de conclusão do fluxo

Uma execução estará concluída quando:

- a copy completa tiver sido analisada;
- cada ponto selecionado tiver sido associado a um tempo do áudio na timeline;
- cada ponto tiver até três alternativas orgânicas e autorizadas, com cinco segundos cada;
- nenhum intervalo da timeline ficar mais de sete segundos sem B-roll visível, ou a lacuna estar explicitamente reportada se não houver candidato relevante e autorizado;
- os arquivos e metadados estiverem registrados no projeto existente;
- os takes estiverem em faixas de vídeo acima do áudio, nos tempos correspondentes;
- o usuário tiver recebido o resumo final e a contagem de takes posicionados.

## 10. Defaults técnicos para a primeira versão

- Obter a faixa de áudio do projeto existente, transcrever localmente com timestamps e alinhar as palavras à copy enviada. A IA usa esses tempos para propor os pontos de B-roll.
- A ferramenta usa sempre as três faixas nomeadas `broll_option_1`, `broll_option_2` e `broll_option_3`, reaproveitando-as entre todos os pontos. Se dois pontos na mesma faixa se sobrepuserem, ajustar os tempos para manter as opções distintas sem conflito, sem criar faixas adicionais.
- Se um candidato tiver menos de cinco segundos ou não puder ser baixado, descartá-lo e buscar substituto. Se não houver três alternativas válidas, inserir as válidas e informar a quantidade faltante.
- Aplicar o limite de sete segundos aos intervalos sem B-roll, incluindo começo e fim do áudio. A primeira versão mede a cobertura de B-roll da ferramenta; a presença de avatar ou outro vídeo base não elimina esse limite.
- A cobertura de um ponto é contada uma vez, mesmo com três opções empilhadas.

## 11. Etapas de implementação

1. Skill criada para pedir a copy e o nome exato do projeto e explicar o fluxo.
2. Ferramentas de transcrição local, alinhamento da copy e medição determinística de cobertura implementadas; falta validar com um projeto real.
3. Cliente FetchLayer TikTok implementado com busca, candidatos baixáveis, estimativa de créditos, download e cache.
4. Ferramenta MCP de inserção implementada para faixas sobrepostas, volume zero e trecho de cinco segundos; falta validar no CapCut.
5. A skill coordena atualizações entre pontos e consulta `broll_coverage` para calcular o marco de um terço e gaps; o fluxo integral ainda precisa ser validado.
6. Skill instalada em `C:\Users\erick\.codex\skills\inserir-takes` para uso global dentro do Codex. A pasta e o formato são específicos do Codex; outros aplicativos de IA precisam importar/adaptar as instruções e oferecer acesso ao MCP.

## 12. Estado inicial da implementação

- A base MCP já consegue abrir projetos existentes em `in_place`, inserir mídia local em faixas nomeadas e salvar alterações no projeto aberto.
- O fluxo manual de busca/download TikTok e inserção de vídeo no projeto CapCut já foi validado pelo usuário.
- O MCP oferece `transcribe_project_audio` para transcrever localmente o áudio da timeline e alinhar palavras à copy, `align_copy_phrase` para localizar frases, e `broll_coverage` para unir intervalos sobrepostos e calcular gaps e o marco de um terço.
- O reconhecimento usa faster-whisper em CPU (`small`, int8) e mantém o modelo e transcrições em cache local dentro da pasta de projetos. O primeiro uso baixa o modelo; depois ele é reaproveitado.
- O cliente FetchLayer/TikTok pesquisa candidatos e baixa MP4s; `add_broll_options` insere de um a três candidatos escolhidos pela IA no mesmo ponto, sempre nas faixas fixas `broll_option_1..3` reutilizadas entre os pontos, com 5 segundos e áudio silenciado, sem salvar automaticamente.
- A imagem Docker inclui as dependências ASR e recebe a chave na inicialização com `--env-file`; os MP4s e o cache do modelo ficam no cache gerenciado dentro da pasta de projetos CapCut.
- Falta validar a extração da faixa de áudio de um projeto real, o alinhamento com o áudio do usuário, a busca/download e a reprodução das três faixas no CapCut. O fluxo ainda não está pronto para anúncios de produção.
- O `quick_validate.py` do skill-creator confirmou a estrutura da skill; a compilação da imagem Docker e a validação de edição em projeto real ainda não foram executadas neste ambiente.

A especificação é evolutiva e deve ser atualizada quando o usuário mudar o fluxo ou acrescentar requisitos.
