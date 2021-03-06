import numpy as np
import matplotlib.pyplot as plt
import matplotlib

def to_percent(y, position):
    # Ignore the passed in position. This has the effect of scaling the default
    # tick locations.
    s = str(100. * y)
    # s = str(y)
    # The percent symbol needs escaping in latex
    if matplotlib.rcParams['text.usetex'] is True:
        return s + r'$\%$'
    else:
        return s + '%'
    
ax      = plt.subplot()
inarr   = np.loadtxt('avg_misfit_vti_gr_mantle_0.txt')
data    = inarr[:, 2]
weights = np.ones_like(data)/float(data.size)
plt.hist(data, bins=20, weights=weights)
outstd  = data.std()
outmean = data.mean()
outmedian   = np.median(data)

# compute mad
from statsmodels import robust
mad     = robust.mad(data)
# plt.xlim(-.2, .2)
import matplotlib.mlab as mlab
from matplotlib.ticker import FuncFormatter
plt.ylabel('Percentage (%)', fontsize=30)
plt.xlabel('Misfit', fontsize=30)
plt.title('Misfit mean = %g , std = %g , mad = %g, median = %g ' %(outmean, outstd, mad, outmedian), fontsize=20)
ax.tick_params(axis='x', labelsize=20)
ax.tick_params(axis='y', labelsize=20)
formatter = FuncFormatter(to_percent)
# Set the formatter
plt.gca().yaxis.set_major_formatter(formatter)
plt.xlim([0., 7.])
plt.xticks(np.arange(7.))
plt.show()