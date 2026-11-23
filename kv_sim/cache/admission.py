"""Cache admission policy for completed requests."""

ADMISSION_POLICIES = ('always', 'no_remote', 'prefix_fit', 'prefix_fit_no_remote',
                      'prefix_fit_pins')


def admitted_blocks(request_hashes, remote_blocks, policy, *, capacity=None, pins=()):
    if policy == "always":
        return request_hashes
    if policy == "no_remote":
        # Never retain an orphan suffix whose remote prefix was not admitted.
        return request_hashes if remote_blocks == 0 else ()
    if policy == 'prefix_fit_no_remote' and remote_blocks:
        return ()
    if policy in ('prefix_fit', 'prefix_fit_no_remote', 'prefix_fit_pins'):
        if capacity is None or capacity <= 0:
            raise ValueError('prefix_fit requires positive cache capacity')
        proposed = request_hashes[:capacity]
        if policy != 'prefix_fit_pins' or not pins:
            return proposed
        # A pinned block already occupies a slot, even if it is absent from
        # this request. Charge one remaining slot per distinct unpinned block.
        unpinned_capacity = capacity - len(pins)
        unpinned_keys = set()
        for index, key in enumerate(proposed):
            if key not in pins:
                unpinned_keys.add(key)
                if len(unpinned_keys) > unpinned_capacity:
                    return proposed[:index]
        return proposed
    raise ValueError("unknown cache admission policy: " + str(policy))
