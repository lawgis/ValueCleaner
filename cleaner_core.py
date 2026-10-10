"""Matching logic for Value Cleaner.

Pure Python (standard library only, no QGIS imports) so it can be tested on
its own. Given a count of distinct text values, it groups spellings that
almost certainly mean the same thing and proposes one correct spelling for
each group.
"""
import math
import re
import unicodedata

DEFAULT_THRESHOLD = 0.85
MIN_FUZZY_LENGTH = 4  # shorter keys are only ever merged on an exact key match


def clean_whitespace(value):
    """Trim the ends and collapse runs of whitespace to a single space."""
    return re.sub(r"\s+", " ", value).strip()


def make_key(value):
    """Comparison key: accents, case, punctuation and spacing are ignored."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[\W_]+", " ", text)
    return text.strip()


def number_signature(key):
    """The numbers inside a key, e.g. 'zone 12b' -> ('12',). Leading zeros ignored."""
    return tuple(str(int(n)) for n in re.findall(r"\d+", key))


def edit_distance(a, b, max_dist=None):
    """Edit distance where swapping two neighbouring letters counts as one edit.

    With max_dist, stops early and returns max_dist + 1 once it is exceeded.
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    la, lb = len(a), len(b)
    if max_dist is not None and la - lb > max_dist:
        return max_dist + 1
    two_back = None
    previous = list(range(lb + 1))
    previous_min = 0
    for i in range(1, la + 1):
        current = [i] + [0] * lb
        ca = a[i - 1]
        row_min = current[0]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == b[j - 1]:
                value = min(value, two_back[j - 2] + 1)
            current[j] = value
            if value < row_min:
                row_min = value
        if max_dist is not None and min(row_min, previous_min) > max_dist:
            return max_dist + 1
        two_back, previous, previous_min = previous, current, row_min
    return previous[lb]


def similarity(a, b):
    """0.0 to 1.0, where 1.0 means identical."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0
    return 1.0 - edit_distance(a, b) / longest


def _within_one(a, b):
    """True if a and b are at most one edit apart (same result as
    edit_distance(a, b) <= 1, but much faster, using string slices)."""
    la, lb = len(a), len(b)
    if la < lb:
        a, b, la, lb = b, a, lb, la          # a is now the longer one
    if la - lb > 1:
        return False
    i = 0
    while i < lb and a[i] == b[i]:
        i += 1
    if i == lb:
        return True                           # equal, or b is a with its last letter dropped
    if la == lb:
        if a[i + 1:] == b[i + 1:]:
            return True                       # one letter different
        return (i + 1 < lb and a[i] == b[i + 1] and a[i + 1] == b[i]
                and a[i + 2:] == b[i + 2:])   # two neighbouring letters swapped
    return a[i + 1:] == b[i:]                 # one extra letter in a


def _close_enough(key_a, key_b, threshold):
    """Fuzzy test for two keys that already share the same numbers."""
    if len(key_a) < MIN_FUZZY_LENGTH or len(key_b) < MIN_FUZZY_LENGTH:
        return False
    allowed = int((1.0 - threshold) * max(len(key_a), len(key_b)) + 1e-9)
    if abs(len(key_a) - len(key_b)) > allowed:
        return False
    if allowed == 0:
        return key_a == key_b
    if allowed == 1:
        return _within_one(key_a, key_b)
    return edit_distance(key_a, key_b, allowed) <= allowed


def _pick_canonical(cleaned_counts):
    """Most frequent spelling; on a tie prefer mixed case, then alphabetical."""
    def rank(item):
        text, count = item
        mixed = not (text.isupper() or text.islower())
        return (-count, 0 if mixed else 1, text)
    return sorted(cleaned_counts.items(), key=rank)[0][0]


class Cluster(object):
    """A group of raw values judged to be spellings of the same thing."""

    def __init__(self, canonical, variants):
        self.canonical = canonical      # proposed correct spelling
        self.variants = variants        # list of (raw_value, feature_count)

    @property
    def total(self):
        return sum(count for _, count in self.variants)

    def changes(self, canonical=None):
        """Raw values that would be rewritten, as {raw: new}."""
        target = self.canonical if canonical is None else canonical
        return {raw: target for raw, _ in self.variants if raw != target}


def build_clusters(counts, threshold=DEFAULT_THRESHOLD):
    """Group raw values and return only the clusters that need a change.

    counts: {raw_value: number_of_features}
    Returns a list of Cluster, largest first.
    """
    raw_by_clean = {}
    clean_counts = {}
    for raw, count in counts.items():
        cleaned = clean_whitespace(raw)
        if not cleaned:
            continue
        raw_by_clean.setdefault(cleaned, []).append((raw, count))
        clean_counts[cleaned] = clean_counts.get(cleaned, 0) + count

    groups = {}  # key -> {cleaned: count}
    for cleaned, count in clean_counts.items():
        groups.setdefault(make_key(cleaned), {})[cleaned] = count

    keys = sorted(groups, key=lambda k: (-sum(groups[k].values()), k))
    # Values containing different numbers are never merged ("Zone 1" is not
    # "Zone 2"), so leaders are bucketed by their numbers and only compared
    # within a bucket. This also keeps large fields fast.
    # Within a bucket, only leaders whose length is in a feasible window are
    # compared: a similar key can't be much shorter or longer.
    leaders_by_numbers = {}  # numbers -> {length: [entry, ...]}
    leaders = []             # entry = [leader_key, [member_keys], creation_index]
    for key in keys:
        by_length = leaders_by_numbers.setdefault(number_signature(key), {})
        length = len(key)
        best = None
        if length >= MIN_FUZZY_LENGTH and threshold > 0:
            low = max(MIN_FUZZY_LENGTH, int(math.ceil(threshold * length - 1e-9)))
            high = int(length / threshold + 1e-9)
            for other_length in range(low, high + 1):
                for leader in by_length.get(other_length, ()):
                    if best is not None and leader[2] > best[2]:
                        continue
                    if _close_enough(leader[0], key, threshold):
                        best = leader
        if best is not None:
            best[1].append(key)
        else:
            entry = [key, [key], len(leaders)]
            by_length.setdefault(length, []).append(entry)
            leaders.append(entry)

    clusters = []
    for _, member_keys, _ in leaders:
        merged = {}
        for key in member_keys:
            merged.update(groups[key])
        canonical = _pick_canonical(merged)
        variants = []
        for cleaned in merged:
            variants.extend(raw_by_clean[cleaned])
        variants.sort(key=lambda rc: (-rc[1], rc[0]))
        cluster = Cluster(canonical, variants)
        if cluster.changes():
            clusters.append(cluster)

    clusters.sort(key=lambda c: (-c.total, c.canonical))
    return clusters
