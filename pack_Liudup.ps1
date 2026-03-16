# ==========================================
# 配置区域
# ==========================================

# 1. 明确需要排除的文件夹（正则匹配）
$ExcludeFoldersRegex = "(\\runs|\\.git|\\venv|\\\.venv|\\__pycache__|\\\.idea|\\\.vscode|\\build|\\dist|\\node_modules)"

# 2. 明确需要排除的文件名或后缀（通配符）
$ExcludeFiles = @(
    "*.pyc", "*.pyo", "*.exe", "*.dll", "*.so", "*.pyd",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.pdf",
    "*.pt", "*.pth", "*.db", "*.sqlite3",
    "package-lock.json", "poetry.lock", ".env", "*.ps1", "LICENSE"
)

# ==========================================
# 逻辑区域
# ==========================================

# 设置输出编码为 UTF8 (兼容中文)
$OutputEncoding = [System.Text.Encoding]::UTF8

# 获取项目根目录
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }

# 获取项目名和上一级目录
$ProjectName = (Get-Item $ProjectRoot).Name
$ParentDir = Split-Path -Path $ProjectRoot -Parent

# 生成输出路径
$Timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$OutputFileName = "${ProjectName}_${Timestamp}.txt"
$OutputPath = Join-Path -Path $ParentDir -ChildPath $OutputFileName

Write-Host "项目目录: $ProjectRoot" -ForegroundColor Yellow
Write-Host "输出路径: $OutputPath" -ForegroundColor Cyan

# 获取文件列表
$Files = Get-ChildItem -Path $ProjectRoot -Recurse -File | Where-Object {
    $item = $_
    if ($item.FullName -match $ExcludeFoldersRegex) { return $false }
    foreach ($pattern in $ExcludeFiles) {
        if ($item.Name -like $pattern) { return $false }
    }
    if ($item.Name -like "${ProjectName}_*.txt") { return $false }
    return $true
}

# 创建/清空文件，并明确指定使用 UTF8 编码
New-Item -ItemType File -Path $OutputPath -Force | Out-Null

$Count = 0
foreach ($file in $Files) {
    $RelativePath = $file.FullName.Replace($ProjectRoot, ".").Replace("\", "/")
    Write-Host "正在打包 [$Count]: $RelativePath" -ForegroundColor Gray
    
    $Header = "`r`n`r`n" + ("=" * 80) + "`r`n" + "FILE: $RelativePath" + "`r`n" + ("=" * 80) + "`r`n"
    
    # 使用 -Encoding UTF8 写入
    Add-Content -Path $OutputPath -Value $Header -Encoding UTF8

    try {
        # 使用 -Encoding UTF8 读取，防止中文乱码
        # 注意：Raw 参数可以保持原始格式
        $Content = Get-Content -Path $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop
        Add-Content -Path $OutputPath -Value $Content -Encoding UTF8
    } catch {
        Add-Content -Path $OutputPath -Value "[警告: 该文件读取失败，可能是非文本格式]" -Encoding UTF8
    }
    $Count++
}

Write-Host "`n成功！已完成打包。" -ForegroundColor Green