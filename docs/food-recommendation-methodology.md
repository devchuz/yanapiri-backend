# Metodología del recomendador alimentario de NutriCRED

Estado: diseño de MVP, pendiente de validación por nutricionista.  
Alcance: niñas y niños de 6 a 59 meses.  
Propósito: apoyo informativo explicable; no diagnostica, prescribe ni reemplaza una consulta.

## 1. Decisión metodológica principal

No debe existir un único algoritmo para todo menor de cinco años:

- **6–23 meses:** solo compiten recetas infantiles oficiales compatibles con el rango exacto de edad. No se cambian ingredientes, cantidades, textura ni porción.
- **24–59 meses:** una receta familiar regional es solo una candidata. No se ofrece como porción infantil hasta que una adaptación sea revisada y aprobada contra la guía oficial de 2–5 años.
- **Menores de 6 meses:** quedan fuera del recomendador de recetas. El bot no debe sugerir alimentación complementaria.

El INS señala que su recetario de 6–23 meses diferencia 6–8, 9–11 y 12–23 meses y considera cantidad, consistencia y alimento de origen animal. Además, las preparaciones fueron validadas en hogares con asesoría de nutricionistas. Esa procedencia justifica tratarlas como un catálogo cerrado y no como texto que un LLM puede modificar: [Recetario nutritivo INS/CENAN](https://repositorio.ins.gob.pe/items/fcef9443-7fff-4941-9e76-ae2ff51d1404).

Para 2–5 años, el INS publica una referencia de 1250 kcal/día para una persona saludable, urbana y con actividad ligera, distribuida por tiempos de comida, además de porciones por grupos. Esa cifra no debe convertirse en una prescripción universal ni aplicarse a una receta familiar completa: [porciones recomendadas del INS para 2–5 años](https://alimentacionsaludable.ins.gob.pe/ninos-y-ninas/porciones-recomendadas/ninos-de-2-5-anos).

## 2. Flujo de extremo a extremo

1. **Resolver el contexto mínimo:** edad en meses calculada desde la fecha de nacimiento, departamento, fecha de recomendación y, solo si corresponde, objetivo nutricional revisado por un profesional.
2. **Aplicar exclusiones:** edad menor de 6 meses, alergias o restricciones no verificadas, signos de peligro, enfermedad que requiera dieta terapéutica o datos insuficientes. En esos casos se orienta al establecimiento de salud.
3. **Seleccionar el catálogo:** recetas infantiles para 6–23 meses; preparaciones regionales con adaptación aprobada para 24–59 meses.
4. **Filtrar por reglas duras:** rango de edad, estado de aprobación, integridad de la fuente y restricciones registradas. Una receta incompatible no recibe un puntaje bajo: se excluye.
5. **Resolver nutrientes:** conservar como valor principal el publicado por el recetario. El cálculo con TPCA es una capa separada de verificación o para recetas sin composición publicada.
6. **Resolver ingredientes:** asociar cada ingrediente a un alimento canónico, luego a una entrada TPCA y por separado a uno o más productos MIDAGRI.
7. **Normalizar cantidades:** usar gramos o mililitros publicados. Una medida casera solo se convierte cuando existe equivalencia documentada y versionada.
8. **Seleccionar precios:** mismo departamento y mes solicitado; si no existe, último mes anterior con una antigüedad máxima configurable de tres meses.
9. **Calcular costo y cobertura:** costo por ingrediente, receta y porción; porcentaje de ingredientes valorizados; fecha real de cada precio y uso de fallback.
10. **Puntuar candidatos:** algoritmo determinista y versionado. La edad y la aprobación son filtros; nutrición, costo, cobertura territorial y actualidad determinan el orden.
11. **Generar 3–5 explicaciones:** por qué es apropiada para la edad, datos nutricionales de la fuente, costo estimado, cobertura y antigüedad. No se generan afirmaciones clínicas libres.
12. **Guardar auditoría:** entradas, reglas, versiones de fuentes, candidatos descartados, puntajes parciales y resultado mostrado.

## 3. Estrategia por edad

### 3.1 De 6 a 23 meses

Reglas obligatorias:

- `child_specific = true`.
- `age_min_months <= age_months <= age_max_months`.
- La receta, textura, ingredientes y porción se muestran sin alteración.
- Los nutrientes publicados por INS/CENAN son los valores de presentación; TPCA no los reemplaza silenciosamente.
- El costo solo se calcula cuando las cantidades pueden expresarse en masa o volumen con una equivalencia trazable.
- El semáforo antropométrico no prescribe una receta. Una regla especial, por ejemplo priorizar preparaciones oficiales con hierro ante anemia confirmada, requiere aprobación clínica y versión propia.

### 3.2 De 24 a 59 meses

Las recetas regionales deben pasar por este ciclo:

1. `unreviewed`: extracción fiel de la receta familiar.
2. `eligible_for_review`: ingredientes y nutrientes completos, sin incompatibilidades evidentes.
3. `reviewed`: un nutricionista documentó qué componentes y porciones oficiales son aplicables.
4. `approved`: puede mostrarse a familias para el grupo de edad indicado.
5. `retired`: deja de recomendarse, pero conserva su historial.

La adaptación aprobada se almacena como una nueva versión; nunca sobrescribe la receta familiar. Debe indicar porción infantil, componentes incluidos, regla oficial utilizada, profesional revisor, fecha y fuente. Si no hay equivalencia oficial o revisión, el sistema puede mostrar el plato al equipo clínico para evaluación, pero no recomendarlo a la familia.

## 4. Datos que se extraen de cada fuente

| Fuente | Campos mínimos | Tratamiento |
|---|---|---|
| Recetario infantil | receta, rango de edad, porción, consistencia, ingredientes, cantidades, preparación, nutrientes, página | Catálogo cerrado y específico para niños |
| Recetario regional | departamento, receta, componentes, porciones familiares, ingredientes, preparación, nutrientes, página | `child_specific=false`; exige adaptación aprobada |
| TPCA | código, nombre, estado o forma del alimento, base de 100 g, nutrientes, edición | Catálogo nutricional maestro; `-1` o ausente se conserva como faltante, no como cero |
| MIDAGRI/SISAP | departamento/mercado, producto y código, unidad, equivalencia kg/L, precio, métrica, año, mes, recuperación | Precio referencial con granularidad y tipo de mercado explícitos |
| Guía 2–5 años | grupo, porción o intercambio, tiempo de comida, población de referencia, fuente | Regla versionada; no se aplica fuera de su población sin revisión |

La TPCA 2023 es la 11. ª edición digital y el INS advierte que la composición puede variar por ambiente, variedad y procesamiento: [repositorio TPCA](https://repositorio.ins.gob.pe/items/945f2705-5700-4561-9281-974adb816604) y [consulta oficial de alimentos](https://tablasperuanas.ins.gob.pe/).

SISAP informa volúmenes, precios y procedencia de productos en mercados mayoristas; por ello el resultado debe decir **precio mayorista referencial**, no precio final que pagará la familia: [servicios de precios MIDAGRI](https://www.gob.pe/institucion/midagri/campa%C3%B1as/3432-precios-de-productos-a-tu-alcance) y [boletines oficiales](https://www.gob.pe/institucion/midagri/informes-publicaciones/1211-precios-de-alimentos).

## 5. Modelo de datos recomendado

Las tablas clínicas existentes no deben mezclarse con catálogos alimentarios. El recomendador puede referenciar `children.id`, pero sus ejecuciones y reglas viven en un módulo separado.

| Tabla | Responsabilidad | Relaciones clave |
|---|---|---|
| `nutrition_sources` | documento, organismo, edición, URL/hash, fecha de ingesta | padre de toda fila extraída |
| `recipes` | identidad estable, tipo infantil/familiar, departamento | tiene muchas versiones |
| `recipe_versions` | texto y nutrientes publicados, porciones, estado de revisión, fuente/página | pertenece a receta y fuente |
| `recipe_age_rules` | edad mínima/máxima y justificación | pertenece a versión de receta |
| `recipe_ingredients` | componente, nombre original, cantidad y unidad originales | pertenece a versión |
| `household_measure_equivalences` | medida, alimento/forma, g o ml, fuente y vigencia | opcional; nunca global sin alimento |
| `canonical_foods` | concepto interno estable | recibe sinónimos y mapeos |
| `food_aliases` | nombre normalizado, región, idioma y fuente | pertenece a alimento canónico |
| `tpca_foods` | fila TPCA versionada y composición por 100 g | se mapea a alimento canónico |
| `market_products` | producto MIDAGRI, presentación y equivalencia | se mapea a alimento canónico |
| `food_mappings` | enlace canónico↔TPCA o canónico↔MIDAGRI | confianza, método, revisor y vigencia |
| `market_prices` | observación por departamento/mercado/mes | pertenece a producto y fuente |
| `nutrition_rules` | umbrales o metas oficiales y población | versionada y aprobada |
| `recipe_cost_runs` | solicitud de costo, cobertura, total, fecha de corte | tiene partidas por ingrediente |
| `recipe_cost_items` | cantidad comprable, precio elegido, fallback y costo | enlaza ingrediente y precio |
| `recommendation_rule_sets` | pesos, filtros y versión del algoritmo | no contiene datos clínicos libres |
| `recommendation_runs` | edad, departamento, fecha, regla y resultado | opcionalmente referencia al niño |
| `recommendation_candidates` | elegibilidad, puntajes, motivos y descarte | pertenece a una ejecución |

No conviene que una sola fila `food_mapping` mezcle simultáneamente ingrediente, TPCA y MIDAGRI. Un ingrediente puede corresponder a una forma TPCA y a varios productos comerciales; son dos decisiones distintas y deben auditarse por separado.

## 6. Matching sin depender del texto exacto

Pipeline reproducible:

1. Conservar `ingredient_original` intacto.
2. Crear `normalized_name`: minúsculas, sin tildes, espacios uniformes y signos controlados.
3. Extraer atributos: alimento base, variedad, estado (`crudo`, `cocido`, `seco`), parte comestible, presentación y región.
4. Buscar alias exacto previamente validado.
5. Proponer candidatos por tokens, sinónimos regionales y similitud de texto.
6. Rechazar automáticamente candidatos con conflicto de forma o presentación.
7. Un revisor acepta el código TPCA y, por separado, uno o más productos MIDAGRI.
8. Guardar `match_method`, `confidence`, `review_status`, `reviewer`, `reviewed_at` y notas.

La similitud solo genera candidatos; nunca convierte un alimento en equivalente. Por ejemplo, arroz cocido de TPCA no es intercambiable con arroz crudo para calcular nutrientes, y un producto comercial específico no representa todas las variedades disponibles.

## 7. Costo regional

Para cada ingrediente con equivalencias validadas:

```text
cantidad_comprable_kg = cantidad_comestible_g / (1000 × rendimiento_comestible)
precio_normalizado = precio_observado / equivalencia_kg_o_l
costo_ingrediente = cantidad_comprable_kg_o_l × precio_normalizado
costo_por_porcion = suma(costo_ingrediente) / porciones_oficiales
```

`rendimiento_comestible` solo se usa si existe una fuente para merma, cáscara o cocción; en caso contrario queda nulo. Tampoco se mezclan kg y litros sin una densidad documentada.

Selección de precio:

```text
departamento = solicitado
producto = mapeo validado
mes_precio <= mes_solicitado
months_old = diferencia mensual
elegir el más reciente con 0 <= months_old <= 3
```

Cada partida conserva `requested_period`, `actual_period`, `months_old`, `fallback_used`, mercado, métrica y fuente. Si falta un precio:

- no se reemplaza por cero;
- se intenta otro producto equivalente solo si el mapeo fue aprobado;
- no se usa otro departamento silenciosamente;
- se calcula cobertura por número de ingredientes y por masa valorizable;
- si falta un ingrediente principal o la cobertura está bajo el umbral del prototipo, el costo se presenta como **incompleto** y no compite como si fuera más barato.

## 8. Ranking determinista y explicable

### Filtros antes del puntaje

- rango de edad compatible;
- receta infantil oficial para 6–23 meses o adaptación aprobada para 24–59;
- fuente y versión disponibles;
- restricciones aplicables revisadas;
- cantidades suficientes para calcular el criterio que se anuncia.

### Puntaje inicial del MVP

La configuración propuesta es una **regla del prototipo**, no una recomendación oficial:

```text
puntaje = 0.40 × nutricion
        + 0.25 × costo_relativo
        + 0.20 × cobertura_territorial
        + 0.15 × actualidad_precio
```

Todos los componentes se expresan entre 0 y 100:

- `nutricion`: cumplimiento acotado de reglas oficiales versionadas. Si no existe una meta por porción aplicable, se usa comparación relativa dentro del mismo grupo etario y se rotula como tal; nunca como porcentaje de adecuación.
- `costo_relativo`: percentil inverso del costo entre candidatos comparables del mismo departamento y periodo. No compara una estimación completa con una incompleta.
- `cobertura_territorial`: proporción de masa valorizable con producto y precio del departamento, con penalización si falta un ingrediente principal.
- `actualidad_precio`: 100 en el mes solicitado y disminuye linealmente hasta 0 al superar tres meses.

La edad no se diluye dentro del puntaje: es una condición de elegibilidad. Los pesos se guardan en `recommendation_rule_sets`, deben aprobarse con el responsable de nutrición y probarse mediante análisis de sensibilidad.

Cada resultado debe exponer algo equivalente a:

> Compatible con 9–11 meses según el recetario INS. Aporta los nutrientes publicados en la fuente. Costo mayorista estimado S/ X por porción, con Y % de ingredientes valorizados en Tumbes; precios de julio de 2026, uno con fallback de un mes.

## 9. Métricas para familia y para auditoría

Para familias, mostrar solo:

- nombre y edad compatible;
- porción o textura oficial;
- energía, proteína y micronutrientes publicados que sean relevantes;
- costo mayorista estimado por porción;
- departamento y mes real del precio;
- cobertura simple: completa o incompleta;
- mensaje breve de que es orientación y no reemplaza al personal de salud.

Para el equipo:

- costo por 100 kcal;
- costo por mg de hierro solo si el hierro es positivo, confiable y el indicador está claramente rotulado como eficiencia de costo, no como calidad global;
- cobertura por ingredientes y masa;
- mapeos pendientes, confianza y validación manual;
- `months_old` máximo y promedio;
- porcentaje de nutrientes publicados frente a calculados;
- versión de regla y fuente.

## 10. Riesgos y controles

| Riesgo | Control |
|---|---|
| Tratar receta familiar como infantil | Estado de aprobación obligatorio y versiones separadas |
| Convertir medidas caseras inventadas | Tabla de equivalencias con fuente; nulo si no existe |
| Confundir crudo/cocido o variedad | Atributos de forma y revisión manual del mapeo |
| Interpretar `-1` TPCA como nutriente negativo o cero | Importador convierte códigos de ausencia en `null` y conserva el valor original |
| Mostrar costo artificialmente bajo | Cobertura mínima y bloqueo si falta ingrediente principal |
| Presentar mayorista como precio del hogar | Etiqueta visible y tipo de mercado en cada resultado |
| Usar un precio viejo o de otra zona | Límite de tres meses y sin fallback territorial silencioso |
| Duplicar nutrientes oficiales con cálculos | Campos `published_*` y `calculated_*` separados |
| Personalizar una dieta terapéutica con el semáforo | Derivación a profesional; solo reglas clínicas aprobadas pueden modificar candidatos |
| Que el LLM invente recetas o cifras | El LLM solo redacta sobre resultados estructurados; no calcula ni completa faltantes |

## 11. Diagnóstico del material disponible al 15 de agosto de 2026

La inspección local muestra:

- 30 recetas infantiles: 10 para cada grupo 6–8, 9–11 y 12–23 meses;
- 233 ingredientes infantiles, ninguno con cantidad normalizada en g/ml;
- 25 recetas familiares de Tumbes y 554 ingredientes; solo 9 cantidades aparecen normalizadas;
- 7870 observaciones MIDAGRI, 26 departamentos/códigos territoriales y meses marzo–agosto de 2026;
- TPCA 2023 disponible como Excel crudo, pero aún no existe catálogo TPCA procesado ni tabla de mapeos validada;
- solo Tumbes tiene actualmente un recetario regional procesado.

Por lo tanto, hoy se puede filtrar y mostrar recetas infantiles con sus nutrientes oficiales, pero **todavía no se puede calcular un costo regional defendible ni recomendar recetas familiares a niños de 2–5 años**. Hacerlo ahora implicaría inventar equivalencias o mapeos.

## 12. MVP de dos departamentos

Orden recomendado:

1. Mantener Tumbes, que ya está procesado, y seleccionar un segundo departamento con recetario oficial y cobertura SISAP; Lima es una opción práctica, pero la decisión debe quedar registrada por el equipo.
2. Versionar documentos y hashes de origen.
3. Procesar TPCA 2023, preservando unidades y faltantes.
4. Seleccionar un subconjunto pequeño: 10 recetas infantiles y, por departamento, 5–10 preparaciones regionales para revisión.
5. Normalizar solo cantidades explícitas o equivalencias documentadas.
6. Validar manualmente los mapeos TPCA y MIDAGRI de los ingredientes de ese subconjunto.
7. Obtener aprobación nutricional de las adaptaciones 24–59 meses.
8. Implementar costo, cobertura, fallback y ranking determinista.
9. Exponer una API que devuelva datos, explicación y trazabilidad; el bot resume 3 opciones y la app muestra el detalle.
10. Probar con datos ficticios y revisar cada salida con nutrición antes de una familia real.

Criterio de terminado: una recomendación puede reconstruirse desde sus fuentes, mapeos, cantidades, precios y versión del algoritmo, sin consultar al LLM.

## 13. Ejemplo reproducible sin presentar datos inventados como reales

Niño ficticio: 7 meses. Departamentos comparados: Tumbes y el segundo departamento elegido por el equipo.

1. El filtro admite exclusivamente recetas 6–8 meses. En los datos actuales aparecen, entre otras, `Purecito verde` (221 kcal, 7.7 g de proteína, 0.7 mg de hierro) y `Zapallito feliz` (153 kcal, 3.3 g de proteína, 0.6 mg de hierro). Esos valores proceden del recetario procesado, no de una estimación.
2. Sus ingredientes conservan medidas como `puñado` o `unidad pequeña`, pero actualmente no tienen gramos validados.
3. En consecuencia, para ambos departamentos el resultado correcto hoy es `cost_status=unavailable`; el ranking puede explicar compatibilidad y nutrientes oficiales, pero no afirmar cuál es más barata.
4. Cuando los gramos y mapeos sean aprobados, el mismo test se ejecuta con los precios regionales seleccionados. Si el costo relativo de A es menor en Tumbes y el de B es menor en el segundo departamento, solo cambia el componente `costo_relativo`; edad, fuente y nutrientes permanecen iguales.
5. La salida guarda las dos listas de partidas y explica exactamente qué precio regional produjo el cambio. No se reutiliza el precio de un departamento para el otro.

Este ejemplo es deliberadamente honesto sobre el faltante actual. Una demostración numérica de costos se añadirá cuando el pipeline produzca al menos dos recetas con 100 % de cantidades normalizadas y mapeos revisados; antes de eso, cualquier costo completo sería ficticio.

### Implementación disponible

`GET /nutrition/recommendations/demo` ejecuta este caso con `Zapallito feliz`,
edad ficticia de 7 meses y precios MIDAGRI de Lima y Tumbes para agosto de 2026.
La papa amarilla se trata como mapeo validado exclusivamente para la demo; el
zapallo macre se conserva como candidato no validado. Ambos precios se muestran,
pero ninguno se convierte en costo de receta porque faltan gramos o mililitros.
