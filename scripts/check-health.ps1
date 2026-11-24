Write-Host "=== Estado del Cluster SolarBI ===" -ForegroundColor Cyan
kubectl get pods,svc,pvc -n solarbi
Write-Host "`n=== Ultimas lineas del Simulador ===" -ForegroundColor Cyan
kubectl logs -n solarbi deployment/simulator --tail=5
