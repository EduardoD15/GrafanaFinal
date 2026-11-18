param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "logs", "clean", "k8s-up", "k8s-status", "k8s-down")]
    [string]$Action = "up",
    [switch]$ForceEtl
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Ensure-EnvFile {
    if (-not (Test-Path ".env")) {
        $envText = Get-Content ".env.example" -Raw
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        $pgBytes = New-Object byte[] 24
        $grafanaBytes = New-Object byte[] 24
        $random.GetBytes($pgBytes)
        $random.GetBytes($grafanaBytes)
        $random.Dispose()
        $pgPassword = [Convert]::ToBase64String($pgBytes).TrimEnd("=").Replace("+", "A").Replace("/", "B")
        $grafanaPassword = [Convert]::ToBase64String($grafanaBytes).TrimEnd("=").Replace("+", "C").Replace("/", "D")
        $envText = $envText.Replace("CAMBIAR_POSTGRES_LOCAL", $pgPassword)
        $envText = $envText.Replace("CAMBIAR_GRAFANA_LOCAL", $grafanaPassword)
        Set-Content -Path ".env" -Value $envText -Encoding Ascii
        Write-Host "Se genero .env local con credenciales aleatorias; no lo comparta ni lo suba al repositorio."
    }
}

function Invoke-DockerCompose {
    param([string[]]$ComposeArgs)
    & docker compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose termino con codigo $LASTEXITCODE."
    }
}

function Ensure-Kubernetes {
    $context = & kubectl config current-context 2>$null
    if ($LASTEXITCODE -ne 0 -or $context -notmatch "docker-desktop") {
        throw "Active Kubernetes en Docker Desktop y seleccione el contexto docker-desktop. Contexto actual: $context"
    }
    & kubectl cluster-info 1>$null
    if ($LASTEXITCODE -ne 0) {
        throw "El cluster de Docker Desktop no responde."
    }
}

function Ensure-KubernetesSecret {
    $values = @{}
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^\s*([^#=]+)=(.*)$") { $values[$matches[1]] = $matches[2] }
    }
    $required = @("POSTGRES_USER", "POSTGRES_PASSWORD", "GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD")
    foreach ($key in $required) {
        if (-not $values.ContainsKey($key)) { throw "Falta $key en .env." }
    }
    & kubectl create secret generic solarbi-secrets -n solarbi `
        "--from-literal=POSTGRES_USER=$($values.POSTGRES_USER)" `
        "--from-literal=POSTGRES_PASSWORD=$($values.POSTGRES_PASSWORD)" `
        "--from-literal=GRAFANA_ADMIN_USER=$($values.GRAFANA_ADMIN_USER)" `
        "--from-literal=GRAFANA_ADMIN_PASSWORD=$($values.GRAFANA_ADMIN_PASSWORD)" `
        --dry-run=client -o yaml | & kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el Secret de Kubernetes." }
}

Ensure-EnvFile

switch ($Action) {
    "up" {
        $archive = Join-Path $Root "data\bronze\opencem-v1.0.0.sqlite.zst"
        if (-not (Test-Path $archive)) {
            throw "No esta $archive. Copie alli el archivo descargado y vuelva a ejecutar .\run.ps1 up."
        }
        Invoke-DockerCompose @("up", "--build")
    }
    "down" { Invoke-DockerCompose @("down") }
    "status" { Invoke-DockerCompose @("ps") }
    "logs" { Invoke-DockerCompose @("logs", "--follow", "--tail", "100") }
    "clean" {
        $confirm = Read-Host "Esto elimina los volumenes de SolarBI en Compose y el namespace solarbi en Kubernetes. Escriba BORRAR SOLARBI"
        if ($confirm -ne "BORRAR SOLARBI") {
            Write-Host "Se cancelo el borrado."
            break
        }

        $kubernetesCleaned = $false
        $contexts = & kubectl config get-contexts -o name 2>$null
        if ($LASTEXITCODE -eq 0 -and $contexts -contains "docker-desktop") {
            & kubectl --context docker-desktop cluster-info 1>$null 2>$null
            if ($LASTEXITCODE -eq 0) {
                & kubectl --context docker-desktop delete namespace solarbi --wait=true --ignore-not-found=true
                if ($LASTEXITCODE -ne 0) { throw "No se pudo eliminar el namespace Kubernetes solarbi." }
                $kubernetesCleaned = $true
            } else {
                Write-Warning "El contexto docker-desktop existe pero el cluster no responde; Kubernetes no se modifico."
            }
        } else {
            Write-Warning "No existe el contexto docker-desktop; solo se limpiara Compose."
        }

        Invoke-DockerCompose @("down", "--volumes", "--remove-orphans")
        if ($kubernetesCleaned) {
            Write-Host "Se eliminaron el namespace solarbi y los volumenes de Compose. La fuente SQLite, .env, imagenes Docker e informes se conservaron."
        } else {
            Write-Warning "Compose quedo limpio, pero los recursos Kubernetes no se borraron porque el cluster no estaba disponible."
        }
    }
    "k8s-up" {
        Ensure-Kubernetes
        $archive = Join-Path $Root "data\bronze\opencem-v1.0.0.sqlite.zst"
        $manifest = Join-Path $Root "data\bronze\opencem-v1.0.0-SHA256SUMS.txt"
        if (-not (Test-Path $archive)) {
            throw "No esta $archive. Copie alli el archivo descargado y vuelva a ejecutar .\run.ps1 k8s-up."
        }
        if (-not (Test-Path $manifest)) {
            throw "No esta $manifest. Restaure el manifiesto oficial SHA-256 antes de continuar."
        }
        & kubectl apply -f ".\k8s\namespace.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el namespace." }
        $pvcJson = & kubectl get pvc -n solarbi -o json
        if ($LASTEXITCODE -ne 0) { throw "No se pudieron consultar las PVC existentes." }
        $postgresPvc = ($pvcJson | ConvertFrom-Json).items |
            Where-Object { $_.metadata.name -eq "postgres-data" }
        if ($postgresPvc -and $postgresPvc.status.phase -eq "Pending" -and
            $postgresPvc.spec.resources.requests.storage -ne "10Gi") {
            if ($postgresPvc.spec.volumeName) {
                throw "El PVC de PostgreSQL tiene un volumen asociado; no se modificara automaticamente."
            }
            & kubectl delete pvc postgres-data -n solarbi --wait=true
            if ($LASTEXITCODE -ne 0) { throw "No se pudo reemplazar el PVC de PostgreSQL pendiente sin datos." }
        }
        & kubectl apply -f ".\k8s\storage.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudieron crear las PVC." }
        & kubectl delete pod dataset-transfer -n solarbi --ignore-not-found=true --wait=true
        & kubectl apply -f ".\k8s\dataset-transfer-pod.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el pod de transferencia." }
        & kubectl wait --for=condition=Ready pod/dataset-transfer -n solarbi --timeout=5m
        if ($LASTEXITCODE -ne 0) { throw "El pod de transferencia no quedo listo." }
        & kubectl cp ".\data\bronze\opencem-v1.0.0.sqlite.zst" "solarbi/dataset-transfer:/data/bronze/opencem-v1.0.0.sqlite.zst"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo transferir la fuente al PVC." }
        & kubectl cp ".\data\bronze\opencem-v1.0.0-SHA256SUMS.txt" "solarbi/dataset-transfer:/data/bronze/opencem-v1.0.0-SHA256SUMS.txt"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo transferir el manifiesto SHA-256 al PVC." }
        & kubectl delete pod dataset-transfer -n solarbi --wait=true
        if ($LASTEXITCODE -ne 0) { throw "No se pudo eliminar el pod temporal." }
        & docker build -t solarbi-etl:1.0.0 .
        if ($LASTEXITCODE -ne 0) { throw "No se pudo construir la imagen solarbi-etl." }
        & kubectl apply -f ".\k8s\configmap.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear la configuracion." }
        Ensure-KubernetesSecret
        & kubectl create configmap grafana-provisioning -n solarbi `
            "--from-file=datasource.yml=.\grafana\provisioning\datasources\datasource.yml" `
            "--from-file=dashboard.yml=.\grafana\provisioning\dashboards\dashboard.yml" `
            "--from-file=alert-rules.yml=.\grafana\provisioning\alerting\alert-rules.yml" `
            --dry-run=client -o yaml | & kubectl apply -f -
        if ($LASTEXITCODE -ne 0) { throw "No se pudo aprovisionar la configuracion de Grafana." }
        & kubectl create configmap grafana-dashboard -n solarbi `
            "--from-file=solarbi-operacion.json=.\grafana\dashboards\solarbi-operacion.json" `
            "--from-file=solarbi-historico.json=.\grafana\dashboards\solarbi-historico.json" `
            --dry-run=client -o yaml | & kubectl apply -f -
        if ($LASTEXITCODE -ne 0) { throw "No se pudo aprovisionar el dashboard de Grafana." }
        & kubectl apply -f ".\k8s\postgres.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo desplegar PostgreSQL." }
        & kubectl apply -f ".\k8s\grafana.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo desplegar Grafana." }
        & kubectl rollout restart deployment/grafana -n solarbi
        if ($LASTEXITCODE -ne 0) { throw "No se pudo reiniciar Grafana con la configuracion actualizada." }
        & kubectl rollout status deployment/grafana -n solarbi --timeout=5m
        if ($LASTEXITCODE -ne 0) { throw "Grafana no quedo listo despues de actualizar su configuracion." }
        & kubectl wait --for=condition=Ready pod -l app=postgres -n solarbi --timeout=10m
        if ($LASTEXITCODE -ne 0) { throw "PostgreSQL no quedo listo." }
        $etlJobJson = & kubectl get job solarbi-etl -n solarbi -o json --ignore-not-found=true
        if ($LASTEXITCODE -ne 0) { throw "No se pudo consultar el estado del Job ETL." }
        $etlJob = $null
        if ($etlJobJson) { $etlJob = $etlJobJson | ConvertFrom-Json }

        $etlActive = 0
        $etlSucceeded = 0
        $etlFailed = 0
        if ($etlJob) {
            if ($etlJob.status.active) { $etlActive = [int]$etlJob.status.active }
            if ($etlJob.status.succeeded) { $etlSucceeded = [int]$etlJob.status.succeeded }
            if ($etlJob.status.failed) { $etlFailed = [int]$etlJob.status.failed }
        }

        $startEtl = $ForceEtl -or -not $etlJob -or ($etlFailed -gt 0 -and $etlActive -eq 0 -and $etlSucceeded -eq 0)
        if ($startEtl) {
            & kubectl delete job solarbi-etl -n solarbi --ignore-not-found=true --wait=true
            if ($LASTEXITCODE -ne 0) { throw "No se pudo reemplazar el Job ETL." }
            & kubectl apply -f ".\k8s\etl-job.yml"
            if ($LASTEXITCODE -ne 0) { throw "No se pudo iniciar el Job del ETL." }
        } elseif ($etlActive -gt 0) {
            Write-Host "El ETL ya esta en curso; se esperara sin reiniciarlo."
        } else {
            Write-Host "El ETL ya completo; se reutilizara su resultado. Use -ForceEtl para recalcularlo."
        }
        & kubectl wait --for=condition=complete job/solarbi-etl -n solarbi --timeout=2h
        if ($LASTEXITCODE -ne 0) { throw "El Job ETL no completo; consulte .\run.ps1 k8s-status y kubectl logs." }
        $reportCopied = $false
        & kubectl delete pod dataset-transfer -n solarbi --ignore-not-found=true --wait=true
        if ($LASTEXITCODE -eq 0) {
            & kubectl apply -f ".\k8s\dataset-transfer-pod.yml"
            if ($LASTEXITCODE -eq 0) {
                & kubectl wait --for=condition=Ready pod/dataset-transfer -n solarbi --timeout=5m
                if ($LASTEXITCODE -eq 0) {
                    & kubectl cp "solarbi/dataset-transfer:/data/bronze/etl-report.json" ".\reports\etl-report.json"
                    if ($LASTEXITCODE -eq 0) { $reportCopied = $true }
                }
                & kubectl delete pod dataset-transfer -n solarbi --ignore-not-found=true --wait=true
                if ($LASTEXITCODE -ne 0) { Write-Warning "No se pudo eliminar el pod temporal de transferencia." }
            }
        }
        if (-not $reportCopied) { Write-Warning "No se pudo copiar el reporte ETL al equipo anfitrion." }
        & kubectl apply -f ".\k8s\simulator.yml"
        if ($LASTEXITCODE -ne 0) { throw "No se pudo iniciar el simulador." }
        Write-Host "SolarBI esta desplegado. En Windows, abra una terminal por cada tunel y mantengalas abiertas:"
        Write-Host "kubectl port-forward -n solarbi service/grafana 30030:3000  # Grafana: http://localhost:30030"
        Write-Host "kubectl port-forward -n solarbi service/postgres-nodeport 30432:5432  # PostgreSQL: localhost:30432"
        Write-Host "Consulte estado: .\run.ps1 k8s-status"
    }
    "k8s-status" {
        Ensure-Kubernetes
        & kubectl get all,pvc -n solarbi
        & kubectl get events -n solarbi --sort-by=.lastTimestamp
    }
    "k8s-down" {
        Ensure-Kubernetes
        Write-Warning "Eliminar el namespace tambien elimina sus PVC y los datos persistidos del taller."
        $confirm = Read-Host "Escriba BORRAR para continuar"
        if ($confirm -eq "BORRAR") {
            & kubectl delete namespace solarbi --wait=true
            if ($LASTEXITCODE -ne 0) { throw "No se pudo eliminar el namespace." }
        } else {
            Write-Host "Se cancelo el borrado."
        }
    }
}
