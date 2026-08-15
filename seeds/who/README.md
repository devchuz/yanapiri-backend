# Referencias antropométricas OMS 2006

Los archivos `wazlms.csv`, `hazlms.csv`, `wfllms.csv` y `wfhlms.csv` fueron
convertidos sin alterar sus valores desde el paquete oficial `igrowup-spss.zip`
de la OMS:

https://cdn.who.int/media/docs/default-source/child-growth/child-growth-standards/software/igrowup-spss.zip

- Descarga y conversión: 2026-08-12
- SHA-256 del ZIP fuente: `1143FE907B38A95B478866D2D6A8570D685176AE27950D18F20F3FD266D0443F`
- `sex=1`: niño; `sex=2`: niña.
- Edad expresada en días; longitud/talla en centímetros.

El cálculo está implementado en `app/domain/anthropometry.py`. No sustituye la
valoración de un profesional de salud.
