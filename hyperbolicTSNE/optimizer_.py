""" Collection of Optimizers for high dimensional data embedding optimization

This file collects several classes, each representing an optimizer to be used in the context of high dimensional
data embedding optimization. Each class is derived from the BaseOptimizer class and therefore has to implement the
following methods:
 - __init__ # Constructs this Optimizer with some initial embedding and a similarity matrix
 - available_params # Pretty print of the parameters this optimizer expects
 - check_params # Checks a given dict whether all necessary parameters are present
 - run # Computes the embedding Y according to the set similarity matrix
"""

from abc import ABC, abstractmethod
import numpy as np
from time import time

from scipy.sparse import csr_matrix

from .solver_ import gradient_descent, ls_newton_solver
from .cost_functions_ import BaseCostFunction, KLDivergence, HyperbolicKL


class BaseOptimizer(ABC):
    """ Optimizer without actual optimization functionality. Serves as a skeleton for other optimizer to build on.
     Provides methods for parameter setting and getting."""

    def __init__(self, *, Y0, V, n_components, other_params=None, verbose=0):
        """
        TODO Method Docu
        """
        if other_params is None:
            other_params = {}
        self._y0 = Y0
        self.Y = Y0
        self.V = V
        self.n_samples = self.V.shape[0]
        self.n_components = n_components
        self._params = None  # Reserves a field for params
        self.params = other_params  # Calls the custom setter to fill the field
        self.verbose = verbose
        self.cf_val = np.inf

    @abstractmethod
    def run(self):
        pass

    @property
    def Y(self):
        return self._y

    @Y.setter
    def Y(self, mat):
        try:
            self._y = mat.reshape(-1, self.n_components)
        except AttributeError:
            self._y = mat

    @property
    def V(self):
        return self._V

    @V.setter
    def V(self, mat):
        self._V = mat

    @property
    def params(self):
        return self._params

    @params.setter
    def params(self,  other_params):
        if self._params is not dict:
            default_params = {}
        else:
            default_params = self._params
        if type(other_params) is not dict:
            raise Exception("`other_params` should be a dict ... initializing with default params")
        else:
            for k, v in other_params.items():
                default_params[k] = v
            BaseOptimizer.check_params(default_params)
            self._params = default_params

    @classmethod
    def check_params(cls, params):
        pass

    @classmethod
    def available_params(cls):
        print("# Base parameters (root of `other_params` dict)")
        print("===============================================")
        print(" BaseOptimizer does not have specific params, please refer to the higher-level optimizers.")
        print("===============================================")


class SequentialOptimizer(BaseOptimizer):
    """ TODO Class Documentation """

    def __init__(self, *, Y0, V, n_components, other_params=None, verbose=0):
        """
        TODO Method Docu
        """
        if other_params is None:
            raise Exception("No `other_params` specified for SequentialOptimizer, please add your params or select "
                            "one of the presets.")
        super(SequentialOptimizer, self).__init__(Y0=Y0, V=V, n_components=n_components, other_params=other_params, verbose=verbose)
        self.cf = self.params["cf"](n_components=self.n_components,
                                    other_params=self.params["cf_config_params"])

    def run(self):
        """ Method that implements the optimization process of this particular optimizer. Inputs are the low-dimensional
        points and the high-dimensional similarity matrix. Output is the time that it took to run the whole loop.
        Attributes in class contain latest values.
        Parameters
        ----------
        Returns
        -------
        Y : numpy array
            Embedding of the high-dimensional data.
        cf_val : float
            Cost function value of the embedding.
        time : float
            Time taken to compute the embedding.
        its_done : int
            Number of iterations performed by the optimizer's solvers.
        """
        # Start: logging
        logging_dict = self.params.get("logging_dict", None)
        if logging_dict is not None:
            logging_dict["log_arrays"] = logging_dict.get("log_arrays", False)
            if logging_dict["log_arrays"]:
                logging_dict["log_arrays_ids"] = logging_dict.get("log_arrays_ids", None)
        solver_counter = 0
        # end: logging
        start = time()
        for se in self.params["sequence"]:
            if se["type"] == "processor":
                self.Y, self.V = se["function"](self.Y, self.V, self.cf, **se["params"])
            elif se["type"] == "solver":
                se["params"]["start_it"] = self.params["solver_its_done"]
                se["params"]["verbose"] = self.verbose
                # Start: logging
                self.Y, self.cf_val, its = se["function"](
                    self.Y, self.cf, dict({"V": self.V}, **self.params["cf_params"]), logging_key="sequential_opt_" + str(solver_counter), logging_dict=logging_dict, **se["params"]
                )
                solver_counter += 1
                # End: logging
                self.params["solver_its_done"] = se["params"]["start_it"] + its
        end = time()

        return self.Y, self.cf_val, end - start, self.params["solver_its_done"]

    # Processors: receive (Y, V, cf, **params) and output Y, V
    @staticmethod
    def list_available_processors():
        # Pretty prints the available processors in this optimizer
        print("# SequentialOptimizer processors")
        print("================================")
        print("All processors receive (Y, V, cf, **params) and output Y, V.")
        print("Available are:")
        print(" - `exaggerate_matrix`: Multiplies the V matrix with a given `exaggeration` parameter.")
        print("- `scale_to_optimal_size`: Performs a binary search for an optimal scale parameter, with search depth "
              "`binary_search_depth` to find the best scale for the given Y and cost function.")
        print("==============================================================")

    # noinspection PyUnusedLocal
    @staticmethod
    def exaggerate_matrix(Y, V, cost_function, exaggeration=1.0):
        """ Multiply the given `V` matrix with the given `exaggeration` scalar and return the result.
        Parameters
        ----------
        Y : numpy array
            Embedding of the high-dimensional data set
        V : csr_matrix
            Probability distribution of the data in high-dimensional space
        cost_function : instance of a BaseCostFunction
            Cost function object instance that can compute the objective and gradient
        exaggeration : float, optional (default=1.0)
            Value to multiply the `V` matrix with.
        Returns
        -------
        Y, V : numpy array, csr_matrix
            Unchanged Y and result of `V * exaggeration`
        """
        return Y, V * exaggeration

    @staticmethod
    def scale_to_optimal_size(Y, V, cost_function, binary_search_depth=10):
        """
        TODO Method Docu
        """
        # Compute the error of the current embedding
        error = cost_function.obj(Y, V=V)

        # Find an initial minimum, encode steps as (exponent, error)
        # TODO This assumes that scaling up will improve the error, include scaling down.
        pre_previous = (1., error)
        previous = (1., error)
        current = (1., error)
        exp = 0
        # TODO Can we make a more reasonable assumption here on the upper bound of the exponent than just arbitrary 15?
        while exp < 15 and current[1] <= previous[1]:
            # Shift computed errors and go to next exponent
            pre_previous = previous
            previous = current
            exp += 1
            # Blow up embedding by current exponent, compute error, and save both exp and error
            Y *= 10 ** exp
            line_search_error = cost_function.obj(Y, V=V)
            Y /= 10 ** exp
            current = (exp, line_search_error)

        if exp >= 16:  # No local minimum was found
            raise Exception("Could not find a suitable scaling factor in the line search.")
        else:  # Program did find a local minimum to start from
            left = pre_previous
            middle = previous
            right = current
            depth = 0
            while depth < binary_search_depth:
                # Increase depth and compute the errors left and right to the middle
                depth += 1
                exp = (left[0] + middle[0]) / 2
                Y *= 10 ** exp
                line_search_error = cost_function.obj(Y, V=V)
                Y /= 10 ** exp
                left_middle = (exp, line_search_error)

                exp = (middle[0] + right[0]) / 2
                Y *= 10 ** exp
                line_search_error = cost_function.obj(Y, V=V)
                Y /= 10 ** exp
                middle_right = (exp, line_search_error)

                # Shift the binary search left or right, depending on the erros found
                if left_middle[1] < middle_right[1]:
                    right = middle
                    middle = left_middle
                else:
                    left = middle
                    middle = middle_right

            # Store the minimum found via line search
            if left[1] <= middle[1] and left[1] <= right[1]:
                Y *= 10 ** left[0]
            elif middle[1] <= left[1] and middle[1] <= right[1]:
                Y *= 10 ** middle[0]
            else:
                Y *= 10 ** right[0]

        return Y, V

    @property
    def params(self):
        return super(SequentialOptimizer, self).params

    @params.setter
    def params(self,  other_params):
        # Nice trick for using super setter: https://gist.github.com/Susensio/979259559e2bebcd0273f1a95d7c1e79
        super(SequentialOptimizer, type(self)).params.fset(self, other_params)
        SequentialOptimizer.check_params(self.params)

    @classmethod
    def check_params(cls, params):
        # Cost function params
        # 1. Check that all necessary params are available
        if "cf" not in params or "cf_config_params" not in params or "cf_params" not in params:
            raise Exception(
                "`other_params` should include a BaseCostFunction object `cf`, its config parameters "
                "`cf_config_params` and its runtime params `cf_params`")
        # 2. Check that cost function param is of type BaseCostFunction
        if BaseCostFunction not in params["cf"].__bases__:
            raise Exception("`cf` should be an derived class of BaseCostFunction")
        # 3. cf_config_params and cf_params should be either None or dict
        if params["cf_config_params"] is not None and type(params["cf_config_params"]) is not dict:
            raise Exception("`cf_config_params` should be either None or a dict with the appropriate setup parameters")
        if params["cf_params"] is not None and type(params["cf_params"]) is not dict:
            raise Exception("`cf_params` should be either None or a dict with the appropriate runtime parameters")

        # Global iteration counter params
        if "solver_its_done" not in params:
            params["solver_its_done"] = 0
        else:
            if type(params["solver_its_done"]) != int:
                raise Exception("solver_its should be an integer equal or larger than 0 (if starting from iteration "
                                "0th)")
            else:
                if params["solver_its_done"] < 0:
                    raise Exception("solver_its should be an integer equal or larger than 0 (if starting from "
                                    "iteration 0th), indicating the solver's starting counting point")

        # Sequence params
        if "sequence" not in params:
            raise Exception("Missing the `sequence` parameter which is an array of solvers and processors. Please "
                            "check the presets for more information.")
        if len(params["sequence"]) == 0:
            print("Warning: there are no blocks in the sequence, therefore the optimizer will not have an effect on "
                  "the embedding,")
        for e in params["sequence"]:
            if "type" not in e or "function" not in e or "params" not in e:
                raise Exception("Each block of the sequence must be a dict with the `type`, `function` and `params` "
                                "entries. Please check the presets for more information.")
            if e["type"] not in ["processor", "solver"]:
                raise Exception("`type` of sequence's block in SequentialOptimizer was not recognized.")
            # TODO: checks for specific types of blocks

    # Sequence param presets and blocks
    @classmethod
    def empty_sequence(cls, cf=KLDivergence, cf_config_params=None, cf_params=None):
        """Empty sequence of the optimizer, serves as a basic template to be appended to.
        Parameters
        ----------
        cf : function or callable, optional (default: KLDivergence)
            Class that implements the method's obj, grad, hess. These methods should return a float, vector and matrix,
            respectively.
        cf_config_params : dict, optional (default: None)
            Additional params that configure the cost function class, e.g., the angle for the KLDivergence.
        cf_params : dict, optional (default: None)
            Additional params that should be passed to the obj, grad, hess functions of cf. For example, the
            high-dimensional matrix V.
        Returns
        -------
        template : dict
            A dict wrapping the cost function, its configuration parameters, further cost function parameters,
            as well as an empty list with potential sequence blocks to be performed.
        """
        print("Please note that `empty_sequence` uses the KL divergence with Barnes-Hut approximation (angle=0.5) by default.")
        if cf_config_params is None:
            cf_config_params = KLDivergence.bh_tsne()
        if cf_params is None:
            cf_params = {}
        template = {"cf": cf, "cf_config_params": cf_config_params, "cf_params": cf_params, "sequence": []}
        return template

    @classmethod
    def sequence_vdm(
            cls, angle=0.5, exaggeration_its=250, exaggeration=12, gradientDescent_its=750, n_iter_check=np.inf,
            threshold_cf=0., threshold_its=-1, threshold_check_size=-1, verbose_solver=0, exact=False, calc_both=False
    ):
        """
        A sequence to mimic the optimization process by van der Maaten, i.e., perform early exaggeration, followed by
        gradient descent steps. Addtional to vdM's sequence, an optional  final scaling line search is performed,
        looking for the optimal scale of the emebdding.

        Parameters
        ----------
        angle : float, optional (default: 0.5)
            Parameter for the cost function.
        exaggeration_its : int, optional (default: 250)
            Number of iterations spent in the early exaggeration phase.
        exaggeration : float, optional (default: 12)
            Factor by which the V matrix is exaggerated in comparison to the W matrix.
        gradientDescent_its : int, optional (default: 750)
            A positive number of gradient descent steps to be performed by the solver.
        n_iter_check : int, optional (default: inf)
            A positive number of iterations, after which the error is evaluated for convergence.
        threshold_cf : float, optional (default: 0.)
            A positive number, if the cost function goes below this, the solver stops.
        threshold_its : int, optional (default: -1)
            A positive number, if the solver performs this number of iterations, it stops.
        threshold_check_size : float, optional (default: -1)
            A positive number, providing the size to which to scale the current embedding when checking its error.
        verbose_solver: int, optional (default: 0)
            A positive or zero integer, indicating how verbose the solver should be.
        Returns
        -------
        template : dict
            A dict wrapping the cost function, its configuration parameters, further cost function parameters,
            as well as a list with sequence blocks representing early exaggeration and gradient descent.
        """
        # Start with an empty sequence
        template = SequentialOptimizer.empty_sequence(cf_config_params=KLDivergence.exact_tsne() if exact else None)

        # Add the blocks necessary for early exaggeration
        template["sequence"] = SequentialOptimizer.add_block_early_exaggeration(
            template["sequence"], earlyExaggeration_its=exaggeration_its, momentum=0.5, learning_rate=200.0,
            exaggeration=exaggeration, n_iter_check=n_iter_check,
            threshold_cf=threshold_cf, threshold_its=threshold_its, threshold_check_size=threshold_check_size,
            verbose_solver=verbose_solver
        )
        # Add the block for gradient descent
        template["sequence"] = SequentialOptimizer.add_block_gradient_descent_with_rescale_and_gradient_mask(
            template["sequence"], gradientDescent_its=gradientDescent_its, n_iter_check=n_iter_check,
            threshold_cf=threshold_cf, threshold_its=threshold_its, threshold_check_size=threshold_check_size,
            verbose_solver=verbose_solver
        )
        return template

    @classmethod
    def sequence_poincare(cls, exaggeration_its=250, exaggeration=12, gradientDescent_its=750,
                          n_iter_check=np.inf, threshold_cf=0., threshold_its=-1, threshold_check_size=-1,
                          learning_rate=0.1, momentum=0.8, vanilla=False, exact=True, calc_both=False, angle=0.5,
                          area_split=False):
        # Start with an empty sequence
        cf_config_params = HyperbolicKL.exact_tsne() if exact else HyperbolicKL.bh_tsne()
        cf_config_params["params"]["calc_both"] = calc_both
        cf_config_params["params"]["area_split"] = area_split

        if not exact:
            cf_config_params["params"]["angle"] = angle

        template = SequentialOptimizer.empty_sequence(cf=HyperbolicKL, cf_config_params=cf_config_params)

        # Add the blocks necessary for early exaggeration
        template["sequence"] = SequentialOptimizer.add_block_early_exaggeration(
            template["sequence"], earlyExaggeration_its=exaggeration_its, momentum=0.5, learning_rate=learning_rate,
            exaggeration=exaggeration, n_iter_check=n_iter_check,
            threshold_cf=threshold_cf, threshold_its=threshold_its, threshold_check_size=threshold_check_size
        )

        template["sequence"][-2]["params"]["vanilla"] = vanilla

        # Add the block for burn in
        # template["sequence"] = SequentialOptimizer.add_block_gradient_descent_with_rescale_and_gradient_mask(
        #     template["sequence"], gradientDescent_its=10, n_iter_check=n_iter_check,
        #     threshold_cf=threshold_cf, threshold_its=threshold_its, threshold_check_size=threshold_check_size,
        #     learning_rate=learning_rate / 10, momentum=momentum, vanilla=vanilla
        # )
        #
        # template["sequence"][-1]["params"]["vanilla"] = vanilla

        # Add the block for gradient descent
        template["sequence"] = SequentialOptimizer.add_block_gradient_descent_with_rescale_and_gradient_mask(
            template["sequence"], gradientDescent_its=gradientDescent_its, n_iter_check=n_iter_check,
            threshold_cf=threshold_cf, threshold_its=threshold_its, threshold_check_size=threshold_check_size,
            learning_rate=learning_rate, momentum=momentum, vanilla=vanilla
        )

        return template

    @classmethod
    def add_block_early_exaggeration(
            cls, sequence, earlyExaggeration_its, momentum=0.5, learning_rate=200.0, exaggeration=12.0, rescale=None,
            n_iter_rescale=np.inf, gradient_mask=np.ones, n_iter_check=np.inf,
            threshold_cf=0., threshold_its=-1, threshold_check_size=-1, verbose_solver=0
    ):
        """A block to perform early exaggeration.
        Parameters
        ----------
        sequence : List
            A list of processing blocks to be sequentially performed by the optimizer (can be an empty list).
        earlyExaggeration_its : int
            A positive number of early exaggeration steps to be performed by the solver.
        momentum : float, optional (default=0.8)
            TODO What is the momentum?
        learning_rate : float, optional (default=200.0)
            TODO What is the learning_rate?
        exaggeration : float, optional (default=12.0)
            The value with which to multiply the V matrix before performing earlyExaggeration_its many gradient descent
            steps.
        rescale : float, optional (default: None)
            Rescale the embedding to have a bbox diagonal of this value whenever rescaling, if None, no rescaling is
            performed.
        n_iter_rescale : int, optional (default: 125)
            During the `rescale_its`, every `n_iter_n_iter_rescale` iteration, the bbox diagonal of the embedding is
            scaled to have length `rescale`.
        gradient_mask : numpy vector, optional (default: np.ones)
            During each solver iteration, apply the gradient descent update only to those elements that have an entry
            "1" in the gradient mask. Other entries should be "0".
        n_iter_check : int, optional (default: inf)
            Number of iterations before evaluating the global error. If the error is sufficiently low, we abort the
            optimization.
        threshold_cf : float, optional (default: 0.)
            A positive number, if the cost function goes below this, the solver stops.
        threshold_its : int, optional (default: -1)
            A positive number, if the solver performs this number of iterations, it stops.
        threshold_check_size : float, optinoal (default: -1)
            A positive number, providing the size to which to scale the current embedding when checking its error.
        verbose_solver: int, optional (default: 0)
            A positive or zero integer, indicating how verbose the solver should be.
        Returns
        -------
        sequence : List
            The given sequence of blocks with three additional blocks appended, representing the exaggeration of the V
            matrix, the gradient descent steps, and the de-exaggeration of the V matrix.
        """
        sequence.append({
                    "type": "processor", "function": SequentialOptimizer.exaggerate_matrix,
                    "params": {"exaggeration": exaggeration}
                })
        sequence.append({
                    "type": "solver", "function": gradient_descent,
                    "params": {
                        "n_iter": earlyExaggeration_its, "momentum": momentum, "learning_rate": learning_rate,
                        "rescale": rescale, "n_iter_rescale": n_iter_rescale, "gradient_mask": gradient_mask,
                        "n_iter_check": n_iter_check, "threshold_cf": threshold_cf, "threshold_its": threshold_its,
                        "threshold_check_size": threshold_check_size, "verbose": verbose_solver
                    }
                })
        sequence.append({
                    "type": "processor", "function": SequentialOptimizer.exaggerate_matrix,
                    "params": {"exaggeration": 1/exaggeration}
                })
        return sequence

    @classmethod
    def add_block_gradient_descent_with_rescale_and_gradient_mask(
            cls, sequence, gradientDescent_its, momentum=0.8, learning_rate=200.0, rescale=None, n_iter_rescale=np.inf,
            gradient_mask=np.ones, n_iter_check=np.inf, threshold_cf=0, threshold_its=-1, threshold_check_size=-1.,
            verbose_solver=0, vanilla=False
    ):
        """A block to perform a specified number of gradient descent steps.
        Parameters
        ----------
        sequence : List
            A list of processing blocks to be sequentially performed by the optimizer (can be an empty list).
        gradientDescent_its : int
            A positive number of gradient descent steps to be performed by the solver.
        momentum : float, optional (default=0.8)
            TODO What is the momentum?
        learning_rate : float, optional (default=200.0)
            TODO What is the learning_rate?
        rescale : float, optional (default: None)
            Rescale the embedding to have a bbox diagonal of this value whenever rescaling, if None, no rescaling is
            performed.
        n_iter_rescale : int, optional (default: 125)
            During the `rescale_its`, every `n_iter_n_iter_rescale` iteration, the bbox diagonal of the embedding is
            scaled to have length `rescale`.
        gradient_mask : numpy vector, optional (default: np.ones)
            During each solver iteration, apply the gradient descent update only to those elements that have an entry
            "1" in the gradient mask. Other entries should be "0".
        n_iter_check : int, optional (default: inf)
            Number of iterations before evaluating the global error. If the error is sufficiently low, we abort the
            optimization.
        threshold_cf : float, optional (default: 0.)
            A positive number, if the cost function goes below this, the solver stops.
        threshold_its : int, optional (default: -1)
            A positive number, if the solver performs this number of iterations, it stops.
        threshold_check_size : float, optinoal (default: -1)
            A positive number, providing the size to which to scale the current embedding when checking its error.
        vanilla: bool, optional (default: True)
            If True, then vanilla gradient descent with a constant learning rate is used.
            Vanilla gradient descent doesn't use fancy tricks like momentum to accelerate convergence.
        verbose_solver: int, optional (default: 0)
            A positive or zero integer, indicating how verbose the solver should be.
        Returns
        -------
        sequence : List
            The given sequence of blocks with one additional block appended, representing the gradient descent
            iterations.
        """
        sequence.append(
            {
                    "type": "solver", "function": gradient_descent,
                    "params": {
                        "n_iter": gradientDescent_its, "momentum": momentum, "learning_rate": learning_rate,
                        "rescale": rescale, "n_iter_rescale": n_iter_rescale, "gradient_mask": gradient_mask,
                        "n_iter_check": n_iter_check, "threshold_cf": threshold_cf, "threshold_its": threshold_its,
                        "threshold_check_size": threshold_check_size, "verbose": verbose_solver, "vanilla": vanilla
                    }
            })
        return sequence

    @classmethod
    def add_block_scale_to_optimal_size(cls, sequence, binary_search_depth=10):
        sequence.append({
                    "type": "processor", "function": SequentialOptimizer.scale_to_optimal_size,
                    "params": {"binary_search_depth": binary_search_depth}
                })
        return sequence

    @classmethod
    def sequence_spectral_direction(
            cls, alpha0=1.0, use_last_alpha=False, n_iter=20, threshold_cf=0., threshold_its=-1,
            threshold_check_size=-1., use_bh=False
    ):
        """
        TODO Method documentation
        """
        print("[optimizer_][sequence_spectral_direction] runs for 20 iterations by default.")
        # print("[optimizer_][sequence_spectral_direction] uses BH-SNE with theta=0.5 by default.")
        print("[optimizer_][sequence_spectral_direction] uses the exact tsne KLDivergence implementation by default.")
        if use_bh or type(use_bh) == int or type(use_bh) == float:
            cf_params = KLDivergence.bh_tsne()
            if type(use_bh) == bool:
                cf_params["params"]["angle"] = 0.1
            else:
                cf_params["params"]["angle"] = float(use_bh)
        else:
            cf_params = KLDivergence.exact_tsne()
        cf_params["hess_method"] = "spectral_direction"
        template = SequentialOptimizer.empty_sequence(cf_config_params=cf_params)
        template["sequence"].append(
            {
                "type": "solver", "function": ls_newton_solver,
                "params": {
                    "alpha0": alpha0,
                    "ls_method": "backtracking",
                    "ls_kwargs": None,
                    "use_last_alpha": use_last_alpha,
                    "n_iter": n_iter,
                    "threshold_cf": threshold_cf,
                    "threshold_its": threshold_its,
                    "threshold_check_size": threshold_check_size
                }
            })
        return template

    @classmethod
    def available_params(cls):
        # Pretty prints the available params in this optimizer
        super(SequentialOptimizer, cls).available_params()
        print("# SequentialOptimizer parameters (root of `other_params` dict)")
        print("==============================================================")
        print("'sequence': [")
        print("    {'type': 'processor', 'function': processorFunction, 'params': {params for processorFunction}}")
        print("    {'type': 'solver', 'function': solverFunction, 'params': {params for solverFunction}}")
        print("    ...")
        print("]")
        print("==============================================================")
