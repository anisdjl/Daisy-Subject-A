# Daisy — Sujet A : Synchronisation & Gestion de Concurrence

Ce dépôt contient l'implémentation du moteur de synchronisation bidirectionnelle entre Daisy et la plateforme partenaire fictive Artisia, en prévenant les surréservations et en isolant l'artisan des défaillances de l'API externe.

---

## PARTIE 1 — Réponses aux questions du Sujet A

### 1. Deux clients réservent la dernière place au même instant (Daisy vs Artisia). Que se passe-t-il ? Le système peut-il surréserver ?

**Ce qui se passe dans mon implémentation :**

- **Côté Daisy** (`POST /daisy/bookings`) : une transaction PostgreSQL s'ouvre et pose un verrou pessimiste strict via `SELECT ... FOR UPDATE` sur la ligne du créneau (`Session`).
- **Côté Artisia** (`POST /webhook/artisia`) : le webhook arrive pour notifier une réservation prise chez eux. Il tente également un `SELECT ... FOR UPDATE` sur ce même créneau.

PostgreSQL force un ordre d'exécution séquentiel :

- **Si Daisy prend le verrou en premier :** le stock Daisy passe à 1, la capacité restante tombe à 0, la transaction commit. Le webhook Artisia prend ensuite le verrou, constate que `available == 0`, refuse d'altérer le stock numérique et enregistre la réservation avec le statut `conflict_overbooked`.
- **Si le webhook Artisia prend le verrou en premier :** le webhook incrémente `partner_booked`, le stock restant tombe à 0, la transaction commit. La requête Daisy tente ensuite de réserver, voit 0 place disponible et renvoie immédiatement une erreur `400 Bad Request: Stock insuffisant`. Le client Daisy est rejeté avant paiement.

**Le système peut-il surréserver dans la réalité ?**

Oui, physiquement. Daisy et Artisia forment un système distribué sans commit à deux phases (2PC). Artisia vend la place sur sa propre plateforme avant d'envoyer le webhook. Si les deux réservations sont validées localement sur leurs plateformes respectives à la même milliseconde, la surréservation dans le monde réel est déjà consommée chez Artisia avant même que le webhook n'atteigne Daisy.

**Comment mon système l'encaisse :**

Il refuse la corruption silencieuse. Le stock local ne descend jamais sous zéro (pas de capacité à -1). La réservation excédentaire est tracée en base avec le statut `conflict_overbooked`, ce qui permet au back-office d'alerter l'artisan pour arbitrage manuel sans casser la cohérence comptable du créneau.

---

### 2. Le partenaire est injoignable pendant 20 minutes. Que voit l'artisan ? Au retour du service, comment rattrapes-tu l'écart ?

**Ce que voit l'artisan pendant la panne :**

L'artisan continue de travailler normalement dans Daisy. Lorsqu'il crée une réservation locale, le système répond en moins de 50 ms.

Grâce au *Transactional Outbox pattern*, l'opération locale est commitée en base avec un ordre de synchronisation stocké dans `outbox_tasks`. L'interface affiche la réservation comme confirmée dans Daisy avec un statut « Synchro partenaire en attente ». L'artisan ne subit aucun freeze d'écran ni aucune erreur 500.

**Comment l'écart est rattrapé au retour du service :**

- **Flux Daisy → Artisia (sortant) :** les tâches `pending` dans `outbox_tasks` sont dépilées.
  - **Piège d'Artisia :** son endpoint `POST /sessions/{id}/bookings` n'est pas idempotent. Pour éviter les doublons lors des retries post-panne, le worker effectue d'abord un `GET /sessions` chez Artisia pour vérifier l'état réel avant de rejouer les réservations, ou pousse un `PATCH /sessions/{id}` avec la capacité restante recalculée.
- **Flux Artisia → Daisy (entrant) :** Artisia rejoue ses webhooks en attente (selon sa politique de retry à 1 min, 5 min, 30 min, 2 h). Dès réception, la table `webhook_events` filtre les doublons éventuels et applique les réservations manquantes sous verrou pessimiste.

---

### 3. Le même webhook arrive trois fois. Comment tu t'en protèges, et à quel coût ?

**La protection :**

Une barrière d'idempotence au niveau du stockage via la table `webhook_events`, dont la clé primaire est l'`event_id`.

À chaque réception de webhook, une tentative d'insertion (`INSERT`) est soumise avec `db.flush()`. Si l'ID est déjà présent, PostgreSQL déclenche immédiatement une violation de contrainte d'unicité (`IntegrityError`). Le code intercepte l'exception, effectue un rollback et retourne immédiatement un `200 OK` avec `{"status": "ignored", "reason": "already_processed"}` pour stopper les retries d'Artisia sans exécuter la logique métier.

**Le coût :**

- **En calcul / latence :** une écriture indexée sur clé primaire B-Tree, soit moins d'une milliseconde d'overhead.
- **En stockage :** une ligne par événement (`event_id` en VARCHAR + timestamp), soit quelques dizaines d'octets. Pour 10 000 événements par jour, cela représente moins de 50 Mo par an.
- **Choix d'arbitrage :** stocker cette clé dans PostgreSQL plutôt que dans un cache Redis garantit l'atomicité transactionnelle : l'écriture de l'événement et la mise à jour des stocks partagent le même moteur sans risque de désynchronisation entre cache et base.

---

### 4. « Ne jamais surréserver » vs « Ne jamais bloquer une vente » : que choisis-tu, et que dis-tu à l'artisan ?

**Mon choix : Ne jamais surréserver.**

**Mon discours à l'artisan :**

> « Si je bloque une vente par précaution, vous perdez un gain potentiel sur un cours. Mais si j'autorise une survente, vous perdez de l'argent réel : Artisia conserve sa commission même si nous annulons, vous devez gérer un client furieux qui se déplace pour rien un samedi après-midi, et vous n'avez pas de 9ème tour de potier physique à lui installer. Daisy est là pour protéger votre sérénité et la réputation de votre atelier, pas pour vous fabriquer des crises en direct. »

---

## PARTIE 2 — Socle commun

### 1. Journal de bord

**Temps passé :** environ 4 heures.

**Ordre d'attaque :**

1. Analyse de `partner-api.md` pour cibler les faiblesses d'Artisia (non-idempotence du POST, latence de 3 à 6 s, webhooks désordonnés ou dupliqués).
2. Modélisation relationnelle (`Session`, `Booking`, `WebhookEvent`, `OutboxTask`) et configuration de PostgreSQL (Supabase).
3. Implémentation du webhook entrant : sécurisation HMAC, idempotence par clé primaire, verrouillage pessimiste `FOR UPDATE`.
4. Implémentation du flux sortant : découplage asynchrone via `BackgroundTasks` et Transactional Outbox pattern.
5. Rédaction des arbitrages et documentation.

**Ce qui a bloqué :** résolution DNS IPv6 par défaut sur l'URL directe de Supabase sous macOS, résolue en basculant sur le connection pooler en mode Session sur le port 5432.

**Ce qui a été délibérément laissé de côté :** un cluster RabbitMQ/Celery complet. Pour la volumétrie demandée et le cadre du test, `BackgroundTasks` de FastAPI combiné à la table `outbox_tasks` démontre le découplage asynchrone sans ajouter de dépendance d'infrastructure inutile.

---

### 2. Tri des tickets

Classement par appétence personnelle (du plus motivant au moins motivant) :

1. Un partenaire a changé le format de ses dates sans prévenir, les réservations n'entrent plus depuis ce matin.
2. Le calendrier du back-office rame dès qu'un atelier a plus de 200 cours affichés.
3. Le tunnel du widget a un taux d'abandon de 60 % à l'étape des coordonnées, personne ne sait pourquoi.
4. Il faut exposer une API publique propre pour que de futurs partenaires s'intègrent seuls.
5. Trois artisans signalent que l'export comptable ne correspond pas à leurs relevés bancaires.
6. Il faut ajouter les paiements sur place par TPE dans le parcours de réservation.
7. Les artisans réclament un filtre par type de cours dans la liste des réservations.
8. Le design system n'existe pas, chaque écran a ses propres boutons.

**Ce que ce classement dit de moi :**

Je suis stimulé par les urgences de production, l'optimisation de performance pure et la traque d'anomalies complexes ayant un impact business direct. Je privilégie la fiabilité des tuyaux et la robustesse architecturale par rapport à la maintenance d'UI ou aux tâches cosmétiques.

---

### 3. Question ouverte : Deux premières semaines dans l'équipe

*Contexte : un produit en production depuis 4 ans avec 70 artisans actifs et une équipe de 3 développeurs.*

**Semaine 1 — Comprendre sans casser :**

- **Jour 1-2 :** installer l'environnement de dev local de zéro, noter chaque point de friction et mettre à jour la documentation d'onboarding.
- **Jour 3 :** prendre 2 ou 3 tickets de support réels et assister à un échange client pour voir comment les artisans utilisent l'outil en pratique.
- **Jour 4-5 :** livrer une première correction mineure (bugfix ou typo) en production pour tester l'intégralité du pipeline CI/CD et le cycle de déploiement.

**Semaine 2 — Cartographie des risques :**

- Auditer les logs d'erreurs (Sentry) et identifier les 3 erreurs les plus récurrentes qui polluent le monitoring.
- Analyser les requêtes lentes et l'usage des index sur les tables à forte écriture (`bookings`, `sessions`).
- Conclure par un point d'étape avec les deux développeurs pour aligner ma vision sur la dette technique critique identifiée sans chercher à tout réécrire.

---

### 4. Ce que j'aurais demandé avant de commencer

**La question technique non posée :**

> « Lorsqu'une surréservation simultanée inévitable se produit entre Artisia et Daisy sur la dernière place disponible, quelle est la règle contractuelle de Daisy : Daisy annule-t-il systématiquement le partenaire pour privilégier sa vente directe, ou applique-t-on la stricte antériorité de l'horodatage (`occurred_at`) ? »

**L'hypothèse retenue pour avancer :**

J'ai supposé que Daisy privilégie la préservation de la cohérence physique de l'atelier sans prise de décision unilatérale destructrice : la réservation partenaire excédentaire est acceptée et marquée en statut de conflit (`conflict_overbooked`), laissant l'artisan arbitrer sans corrompre le stock réel du cours.

---

## Architecture technique & Choix d'implémentation

| Domaine | Choix |
|---|---|
| **Framework** | FastAPI (Python 3.13) |
| **Base de données** | PostgreSQL (hébergé sur Supabase via connection pooler Session) |
| **ORM** | SQLAlchemy 2.0 |
| **Stratégie de concurrence** | Pessimistic Locking (`SELECT ... FOR UPDATE`) |
| **Stratégie d'asynchronisme** | Transactional Outbox Pattern + FastAPI Background Tasks |
| **Sécurité** | Validation de signature cryptographique HMAC-SHA256 sur les webhooks entrants |