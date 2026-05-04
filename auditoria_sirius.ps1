# Caminhos do projeto S.I.R.I.U.S.
$projeto = "C:\Users\carlos\Documents\projetofacu\Sistema_ChatBot"
$destino = "C:\Users\carlos\Documents\cerebro\Relatorio_SIRIUS_4h.md"

# Lê os arquivos principais para análise
$codigo = Get-Content "$projeto\main_residente.py", "$projeto\SiriusApp.jsx" -Raw
$dataHora = Get-Date -Format "dd/MM/yyyy HH:mm"

# Executa a IA local e salva o resultado
$header = "# 🕒 Auditoria Recorrente S.I.R.I.U.S. - $dataHora`n"
$header | Out-File $destino -Encoding utf8
$codigo | ollama run gemma4 "Analise o código do projeto S.I.R.I.U.S. Identifique erros de lógica e segurança. Responda em Português para Obsidian." >> $destino