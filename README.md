# Simulation copy-trading Polymarket

Simulation **sans argent réel** : toutes les 10 minutes, GitHub lance le programme qui
regarde les trades publics de 3 traders et fait comme si on les copiait avec 10 $,
au prix réellement disponible à ce moment-là.

**Résultats : ouvrir [RAPPORT.md](RAPPORT.md)** (mis à jour automatiquement).

## Règles de copie
- On copie seulement le **premier achat** d'un trader sur un résultat (pas ses renforts).
- On copie même si le prix a monté par rapport au sien (test 2).
- On n'achète pas si le prix a **chuté de plus de 5 centimes** (match sans doute déjà joué).
- On n'achète pas en dessous de **0,05 $** ni au-dessus de **0,95 $**.
- On ignore un trade vu **plus de 20 minutes** après le trader.
- Si le trader revend, on revend aussi au meilleur prix acheteur.
- À la fin du match : 1 $ par part si gagné, 0 sinon.
- Les trades passés avant le lancement ne sont pas comptés.

## Modifier les réglages
Ouvrir `config.json` → icône crayon → changer un chiffre → « Commit changes ».

## Lancer à la main
Onglet **Actions** → « Simulation copy-trading » → **Run workflow**.

Aucune clé, aucun compte Polymarket : le programme ne fait que lire des données publiques.

## Historique
Test 1 (24 oct – 7 nov 2026) : `archives/test1-oct-nov/` — non concluant, GitHub ne lançait le programme que toutes les ~5 h.
