/* Generated by Cython 0.25.2 */

#ifndef __PYX_HAVE__modparam
#define __PYX_HAVE__modparam


#ifndef __PYX_HAVE_API__modparam

#ifndef __PYX_EXTERN_C
  #ifdef __cplusplus
    #define __PYX_EXTERN_C extern "C"
  #else
    #define __PYX_EXTERN_C extern
  #endif
#endif

#ifndef DL_IMPORT
  #define DL_IMPORT(_T) _T
#endif

__PYX_EXTERN_C DL_IMPORT(std::vector<float> ) bspl_basis(int, int, float, float, float, int);

#endif /* !__PYX_HAVE_API__modparam */

#if PY_MAJOR_VERSION < 3
PyMODINIT_FUNC initmodparam(void);
#else
PyMODINIT_FUNC PyInit_modparam(void);
#endif

#endif /* !__PYX_HAVE__modparam */