# Etica, seguridad y privacidad

Los registros de contexto se minimizan antes de persistir: se descarta `textual_description` y solo se conserva `inverter`, `source`, `device` y `task_type`. Verifique que las categorias permitidas no identifiquen a una persona antes de exponerlas. Trate tambien el dataset publico conforme a la Ley 1581 de 2012, limite el acceso, finalidad y retencion, y documente responsable/base legal con el docente. Evite incluir contexto de usuarios en capturas o exportaciones.

Las claves viven en `.env` local (Git ignorado) y como Secret de Kubernetes; no se incluyen en la imagen. El modo inicial publica puertos solo para desarrollo local, no exponga 5432/3000 a Internet. Para uso compartido habilite TLS, roles de solo lectura para Power BI, rotacion de secretos y SMTP de alertas. Kubernetes Secret sin cifrado externo no equivale a una boveda: no comparta el archivo del cluster ni sus credenciales.
