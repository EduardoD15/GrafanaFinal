# Guia Rapida de Verificacion y Resiliencia en Kubernetes

## 1. Estado general del namespace solarbi
- kubectl get all,pvc,configmap,secret -n solarbi

## 2. Inspeccion de logs (ETL y Simulador)
- kubectl logs -n solarbi job/solarbi-etl --tail=20
- kubectl logs -n solarbi deployment/simulator --tail=15

## 3. Prueba de alta disponibilidad y persistencia
- kubectl delete pod postgres-0 -n solarbi
- kubectl get pods -n solarbi -w

Al recrearse el pod postgres-0, los datos historicos y simulados permanecen intactos gracias al PersistentVolumeClaim (postgres-data).
