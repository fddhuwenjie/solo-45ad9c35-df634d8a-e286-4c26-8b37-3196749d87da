# -*- coding: utf-8 -*-
"""内置样例：养蜂手册《The Bee Keeper's Companion》旧版 10 页 / 新版 12 页。

刻意制造的换版情形：
  * 新版多出“第二版前言”，第 1 章整体后移（换版偏移）；
  * 旧版第 2 页（解剖）在新版拆成第 3、4 页（段落拆分）；
  * 旧版第 5 页尾部与第 6 页在新版合并到第 7、8 页（跨页合并）；
  * 旧版“蜂毒”句在新版被改写（条目词消失）；
  * 索引 CSV 中预置：倒置范围、重复参见、缺失交叉引用目标、主子条目矛盾。
"""

OLD_TEXT = """=== PAGE 1 ===
Chapter 1. The World of the Honey Bee

The honey bee is among the most studied of all social insects. A single colony may contain tens of thousands of workers, one queen, and a few hundred drones during the summer months. Foragers travel several kilometres from the hive and return with nectar, pollen, water, and resin.

Beekeepers have kept honey bees for thousands of years. The craft combines husbandry, observation, and patience, and rewards the keeper with pollination, wax, and honey.

=== PAGE 2 ===
Anatomy of a Worker Bee

The worker bee carries a coat of branched hairs that traps pollen and an electrostatic charge. Her antennae detect odours, humidity, and the faint vibrations of the famous waggle dance performed on the comb.

Mouthparts include a sucking proboscis for nectar and strong mandibles for shaping wax. On the hind legs sit the pollen baskets, and four translucent wings beat more than two hundred times per second. A barbed stinger at the tip of the abdomen carries a small load of venom and tears free after use.

=== PAGE 3 ===
Lifespan and Reproduction

Summer workers live only six weeks, labouring from their first hour as cell cleaners to their final flights as foragers. A queen, by contrast, may lay for three or four years.

Drones develop from unfertilised eggs and exist solely to mate with a virgin queen on her nuptial flight.

The Waggle Dance

When a forager returns from a rich food source, she performs a figure-eight run on the vertical comb known as the waggle dance. Audience bees follow her with antennae outstretched, reading the performance.

=== PAGE 4 ===
The angle of the run against gravity encodes direction relative to the sun, while the duration of the central waggling segment encodes distance. A lively run one second long advertises nectar roughly a kilometre away.

Karl von Frisch decoded the dance language in the twentieth century and showed that recruits fly directly to the advertised location even over unfamiliar terrain.

The Queen

The queen alone produces the colony's queen substance, a pheromone blend passed from bee to bee that suppresses rival queen cells and signals her presence throughout the nest.

=== PAGE 5 ===
Early in her life the queen takes a single nuptial flight, mating in the air with a dozen drones, and stores the sperm for years of continuous laying.

When her pheromone signal weakens, workers raise new queens in waxen cups. The old queen then leaves with a swarm, while a young queen inherits the nest.

Piping and Swarm Preparation

Before a swarm departs, worker bees run through the hive piping, a sharp rising note produced by wing muscles pressed against the comb.

=== PAGE 6 ===
Chapter 2. Hives and Swarms

Modern hives use movable frames that let the beekeeper inspect every comb without destroying it. The brood nest sits below, honey stores above, separated by a queen excluder.

Piping travels through the wax as vibration rather than air. Experienced keepers press an ear to the hive or use an electronic microphone to hear it in the days before swarming.

Swarming is the colony's natural reproduction: about half the bees leave with the old queen, gather in a pendant cluster on a nearby branch, and send scouts to evaluate possible new homes.

=== PAGE 7 ===
The Honeycomb

Worker bees secrete thin flakes of wax from glands on the abdomen and shape them into hexagonal cells. The hexagon minimises wax for a given volume and braces the comb in every direction.

Fresh comb is white and fragile; it darkens with layers of brood cocoons and propolis. Cells for honey are built slightly upward so that the load never runs out.

=== PAGE 8 ===
Chapter 3. Harvest and Honey

Harvesting begins when the keeper shakes and brushes bees from capped frames. A brush with long, soft bristles works best; quick downward sweeps roll the bees off without crushing them.

Frames then move to the extracting room, where a heated knife slices the wax cappings and a centrifugal extractor spins the honey from the cells.

The strained honey rests in a settling tank for two days so that air and fine wax rise before bottling.

=== PAGE 9 ===
Honey varies with forage: clover gives a mild, light crop; buckwheat a dark and assertive one; lime blossom a pale green honey with a minty finish.

Beekeepers describe these differences as varietals, and label each harvest with district and season.

Never feed extracted honey back to bees from an unknown source, because spores of American foulbrood can survive in it and destroy an apiary.

=== PAGE 10 ===
Winter Stores

A colony winters on sealed honey and bee bread, clustering around the queen when temperatures fall. Stores of roughly twenty kilograms are needed in temperate climates.

The Smoker

The smoker is the beekeeper's oldest tool: a smouldering bundle of hessian sends cool white smoke into the entrance. Smoke triggers the feeding response that precedes abandonment of the hive and masks alarm pheromone, making inspection possible.

Keepers keep the fuel smouldering rather than flaming and empty the firebox only on bare soil.
"""

NEW_TEXT = """=== PAGE 1 ===
Foreword to the Second Edition

This second edition has been reset in a new typeface with wider margins. Page numbers have shifted throughout, several chapters begin on fresh leaves, and a few paragraphs now break across pages or merge with their neighbours.

As one old writer put it, the hive rewards the keeper with pollination, wax, and honey; that partnership is the subject of this book.

=== PAGE 2 ===
Chapter 1. The World of the Honey Bee

The honey bee is among the most studied of all social insects. A single colony may contain tens of thousands of workers, one queen, and a few hundred drones during the summer months. Foragers travel several kilometres from the hive and return with nectar, pollen, water, and resin.

Beekeepers have kept honey bees for thousands of years. The craft combines husbandry, observation, and patience, and rewards the keeper with pollination, wax, and honey.

=== PAGE 3 ===
Anatomy of a Worker Bee

The worker bee carries a coat of branched hairs that traps pollen and an electrostatic charge. Her antennae detect odours, humidity, and the faint vibrations of the famous waggle dance performed on the comb.

=== PAGE 4 ===
Mouthparts include a sucking proboscis for nectar and strong mandibles for shaping wax. On the hind legs sit the pollen baskets, and four translucent wings beat more than two hundred times per second. A barbed stinger at the tip of the abdomen carries a small defensive load and tears free after use.

Lifespan and Reproduction

Summer workers live only six weeks, labouring from their first hour as cell cleaners to their final flights as foragers. A queen, by contrast, may lay for three or four years.

Drones develop from unfertilised eggs and exist solely to mate with a virgin queen on her nuptial flight.

=== PAGE 5 ===
The Waggle Dance

When a forager returns from a rich food source, she performs a figure-eight run on the vertical comb known as the waggle dance. Audience bees follow her with antennae outstretched, reading the performance.

The angle of the run against gravity encodes direction relative to the sun, while the duration of the central waggling segment encodes distance. A lively run one second long advertises nectar roughly a kilometre away.

=== PAGE 6 ===
Karl von Frisch decoded the dance language in the twentieth century and showed that recruits fly directly to the advertised location even over unfamiliar terrain.

The Queen

The queen alone produces the colony's queen substance, a pheromone blend passed from bee to bee that suppresses rival queen cells and signals her presence throughout the nest.

=== PAGE 7 ===
Early in her life the queen takes a single nuptial flight, mating in the air with a dozen drones, and stores the sperm for years of continuous laying.

When her pheromone signal weakens, workers raise new queens in waxen cups. The old queen then leaves with a swarm, while a young queen inherits the nest.

Piping and Swarm Preparation

Before a swarm departs, worker bees run through the hive piping, a sharp rising note produced by wing muscles pressed against the comb. Piping travels through the wax as vibration rather than air. Experienced keepers press an ear to the hive or use an electronic microphone to hear it in the days before swarming.

=== PAGE 8 ===
Chapter 2. Hives and Swarms

Modern hives use movable frames that let the beekeeper inspect every comb without destroying it. The brood nest sits below, honey stores above, separated by a queen excluder.

Swarming is the colony's natural reproduction: about half the bees leave with the old queen, gather in a pendant cluster on a nearby branch, and send scouts to evaluate possible new homes.

=== PAGE 9 ===
The Honeycomb

Worker bees secrete thin flakes of wax from glands on the abdomen and shape them into hexagonal cells. The hexagon minimises wax for a given volume and braces the comb in every direction.

Fresh comb is white and fragile; it darkens with layers of brood cocoons and propolis. Cells for honey are built slightly upward so that the load never runs out.

=== PAGE 10 ===
Chapter 3. Harvest and Harvesting Honey

Harvesting begins when the keeper shakes and brushes bees from capped frames. A brush with long, soft bristles works best; quick downward sweeps roll the bees off without crushing them.

Frames then move to the extracting room, where a heated knife slices the wax cappings and a centrifugal extractor spins the honey from the cells.

The strained honey rests in a settling tank for two days so that air and fine wax rise before bottling.

=== PAGE 11 ===
Honey varies with forage: clover gives a mild, light crop; buckwheat a dark and assertive one; lime blossom a pale green honey with a minty finish.

Beekeepers describe these differences as varietals, and label each harvest with district and season.

Never feed extracted honey back to bees from an unknown source, because spores of American foulbrood can survive in it and destroy an apiary.

=== PAGE 12 ===
Wintering, Smoking, and Closing Notes

A colony winters on sealed honey and bee bread, clustering around the queen when temperatures fall. Stores of roughly twenty kilograms are needed in temperate climates.

The smoker is the beekeeper's oldest tool: a smouldering bundle of hessian sends cool white smoke into the entrance. Smoke triggers the feeding response that precedes abandonment of the hive and masks alarm pheromone, making inspection possible.

Keepers keep the fuel smouldering rather than flaming and empty the firebox only on bare soil.
"""

SAMPLE_CSV = """term,subterm,kind,target,pages
Anatomy,,,,2
Beekeeping,,,,1-10
Bees,,see,Honey Bees,
Honey,,,,9
Honey,varietals,term,,9-10
Honey Bees,,,,1
Lifespan,,,,3
Piping,,,,5
Queen,,,,4-5
Smoker,,,,10-9
Swarming,,,,6
Venom,,,,2
Waggle dance,,,,3-4
Wax,,,,7
Winter stores,,,,10
Honey,,seealso,Honey Bees,
Honey,,seealso,Honey Bees,
Royal jelly,,see,Jelly production,
Honeycomb,,,,7
Extraction,,,,8
"""


def load_sample(conn, name="样例：养蜂手册换版（旧 10 页 / 新 12 页）"):
    from .importer import parse_paged_text, parse_index_csv
    cur = conn.execute(
        "INSERT INTO projects (name) VALUES (?)", (name,))
    pid = cur.lastrowid
    for page_no, label, content in parse_paged_text(OLD_TEXT):
        conn.execute(
            "INSERT INTO pages (project_id, edition, page_no, label, content) VALUES (?,?,?,?,?)",
            (pid, "old", page_no, label, content))
    for page_no, label, content in parse_paged_text(NEW_TEXT):
        conn.execute(
            "INSERT INTO pages (project_id, edition, page_no, label, content) VALUES (?,?,?,?,?)",
            (pid, "new", page_no, label, content))

    parent_ids = {}
    for i, row in enumerate(parse_index_csv(SAMPLE_CSV)):
        parent_id = None
        if row["subterm"] and row["kind"] == "term":
            prow = conn.execute(
                "SELECT id FROM entries WHERE project_id=? AND term=? AND subterm IS NULL",
                (pid, row["term"])).fetchone()
            parent_id = prow["id"] if prow else None
            if parent_id:
                parent_ids[(row["term"], row["subterm"])] = parent_id
        cur = conn.execute(
            "INSERT INTO entries (project_id, term, subterm, kind, ref_target, parent_id, sort_order)"
            " VALUES (?,?,?,?,?,?,?)",
            (pid, row["term"], row["subterm"], row["kind"], row["target"], parent_id, i))
        entry_id = cur.lastrowid
        for s, e, raw_chunk in row["ranges"]:
            if s is None:
                continue
            conn.execute(
                "INSERT INTO locators (entry_id, old_start, old_end) VALUES (?,?,?)",
                (entry_id, s, e))
    conn.commit()
    return pid
