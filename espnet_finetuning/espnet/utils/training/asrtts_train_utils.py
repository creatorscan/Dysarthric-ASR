def mask_by_length_and_multiply(xs, length, fill=0, msize=1):
    assert xs.size(0) == len(length)
    ret = xs.data.new(xs.size(0) * msize, xs.size(1), xs.size(2)).fill_(fill)
    k = 0
    new_length = length.new(len(length) * msize)
    for i, l in enumerate(length):
        for j in range(msize):
            ret[k, :l] = xs[i, :l]
            new_length[k] = length[i]
            k += 1
    return ret, new_length

def set_requires_grad(nets, requires_grad=False):
    """Set requies_grad=Fasle for all the networks to avoid unnecessary computations
    Parameters:
    nets (network list)   -- a list of networks
    requires_grad (bool)  -- whether the networks require gradients or not
    """
    if not isinstance(nets, list):
        nets = [nets]
        for net in nets:
            if net is not None:
                for param in net.parameters():
                    param.requires_grad = requires_grad

