"""Cache admission policy for completed requests."""

ADMISSION_POLICIES = ('always', 'no_remote', 'prefix_fit', 'prefix_fit_no_remote')


def admitted_blocks(request_hashes, remote_blocks, policy, *, capacity=None):
    if policy == "always":
        return request_hashes
    if policy == "no_remote":
        # Never retain an orphan suffix whose remote prefix was not admitted.
        return request_hashes if remote_blocks == 0 else ()
    if policy == 'prefix_fit_no_remote' and remote_blocks:
        return ()
    if policy in ('prefix_fit', 'prefix_fit_no_remote'):
        if capacity is None or capacity <= 0:
            raise ValueError('prefix_fit requires positive cache capacity')
        return request_hashes[:capacity]
    raise ValueError("unknown cache admission policy: " + str(policy))
