# Copyright 2020 Google
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Dict, List, Optional
import numpy as np

from openfermion.linalg import wedge
from openfermion.ops import InteractionRDM

import recirq.hfvqe.util as ccu
from recirq.hfvqe.objective import generate_hamiltonian


def kdelta(i: int, j: int):
    """Kronecker delta function."""
    return 1.0 if i == j else 0.0


def trace_distance(rho: np.ndarray, sigma: np.ndarray) -> float:
    """Compute the trace distance between two matrices.

    Args:
        rho: matrix 1
        sigma: matrix 2

    Returns:
        The trace distance.
    """
    return 0.5 * np.linalg.norm(rho - sigma, 1)


def compute_opdm(
        results_dict: Dict,
        return_variance: Optional[bool] = False):  # testpragma: no cover
    """Take experimental results and compute the 1-RDM.

    Args:
        results_dict: Results dictionary generated from
            OpdmFunctional._calculate_data
        return_variance: Optional return covariances of the opdm
    """
    qubits = results_dict['qubits']
    num_qubits = len(qubits)
    opdm = np.zeros((num_qubits, num_qubits))
    variance_dict = {'xy_even': {}, 'xy_odd': {}, 'z': {}}
    for circuit_idx, permutation in enumerate(
            results_dict['qubit_permutations']):
        for pair_idx in range(num_qubits - 1):
            if pair_idx % 2 == 0:
                data = results_dict['xy_even'][circuit_idx]
            else:
                data = results_dict['xy_odd'][circuit_idx]
            q0, q1 = qubits[pair_idx:pair_idx + 2]
            qA, qB = permutation[pair_idx:pair_idx + 2]
            opdm[qA, qB] += np.mean(data[q1] - data[q0], axis=0) * 0.5
            opdm[qB, qA] += np.mean(data[q1] - data[q0], axis=0) * 0.5

        if return_variance:
            # get covariance matrices
            even_pairs = [
                qubits[idx:idx + 2] for idx in np.arange(0, num_qubits - 1, 2)
            ]
            odd_pairs = [
                qubits[idx:idx + 2] for idx in np.arange(1, num_qubits - 1, 2)
            ]
            data = results_dict['xy_even'][circuit_idx]
            even_cov_mat = np.zeros((len(even_pairs), len(even_pairs)),
                                    dtype=float)
            for ridx, (q0_a, q1_a) in enumerate(even_pairs):
                for cidx, (q0_b, q1_b) in enumerate(even_pairs):
                    for qid_a, coeff_a in [(q0_a, -0.5), (q1_a, 0.5)]:
                        for qid_b, coeff_b in [(q0_b, -0.5), (q1_b, 0.5)]:
                            # get Cov(qid_a, qid_b)
                            # Cov-mat is symmetric so only need upper right val
                            even_cov_mat[ridx, cidx] += (
                                coeff_a * coeff_b *
                                data[[qid_a, qid_b]].cov().to_numpy()[0, 1])
                    # divide covariance by number of samples
                    # because CLT converges to N(mu, sigma**2 / n_samples)
                    even_cov_mat[ridx, cidx] /= len(data[q0_a])

            variance_dict['xy_even'][circuit_idx] = even_cov_mat
            w, _ = np.linalg.eigh(even_cov_mat * len(data[q0_a]))
            if not np.all(w >= 0):
                raise ValueError(
                    "covariance matrix for xy_even:{} not postiive semidefinite"
                    .format(circuit_idx))

            data = results_dict['xy_odd'][circuit_idx]
            odd_cov_mat = np.zeros((len(odd_pairs), len(odd_pairs)),
                                   dtype=float)
            for ridx, (q0_a, q1_a) in enumerate(odd_pairs):
                for cidx, (q0_b, q1_b) in enumerate(odd_pairs):
                    for qid_a, coeff_a in [(q0_a, -0.5), (q1_a, 0.5)]:
                        for qid_b, coeff_b in [(q0_b, -0.5), (q1_b, 0.5)]:
                            odd_cov_mat[ridx, cidx] += (
                                coeff_a * coeff_b *
                                data[[qid_a, qid_b]].cov().to_numpy()[0, 1])
                    odd_cov_mat[ridx, cidx] /= len(data[q0_a])

            variance_dict['xy_odd'][circuit_idx] = odd_cov_mat
            w, _ = np.linalg.eigh(odd_cov_mat * len(data[q0_a]))
            if not np.all(w >= 0):
                raise ValueError(
                    "covariance matrix for xy_odd:{} not postiive semidefinite".
                    format(circuit_idx))
            assert np.all(w >= 0)

        if circuit_idx == 0:  # No re-ordering
            for qubit_idx, q in enumerate(qubits):
                data = results_dict['z'][circuit_idx][q]
                opdm[qubit_idx, qubit_idx] = np.mean(data, axis=0)

            if return_variance:
                variance_dict['z'][circuit_idx] = \
                results_dict['z'][circuit_idx][qubits].cov().to_numpy() / \
                len(data)

    if return_variance:
        return opdm, variance_dict

    return opdm


def covariance_construction_from_opdm(opdm: np.ndarray,
                                      num_samples: int):  # testpragma: no cover
    """Covariance generation from the opdm is from a Gaussian state.

    Args:
        opdm: 1-RDM
        num_samples: Number of samples to estimate the 1-RDM

    Returns:
        Dictionary of covariances
    """
    num_qubits = opdm.shape[0]
    qubit_permutations = ccu.generate_permutations(num_qubits)
    variance_dict = {'xy_even': {}, 'xy_odd': {}, 'z': {}}

    def cov_func(i, j, p, q):
        return opdm[i, q] * kdelta(j, p) - opdm[i, q] * opdm[p, j]

    for circuit_idx, permutation in enumerate(qubit_permutations):
        e_real_pairs = [
            permutation[idx:idx + 2] for idx in np.arange(0, num_qubits - 1, 2)
        ]
        o_real_pairs = [
            permutation[idx:idx + 2] for idx in np.arange(1, num_qubits - 1, 2)
        ]
        even_cov_mat = np.zeros((len(e_real_pairs), len(e_real_pairs)),
                                dtype=float)
        for ridx, (i, j) in enumerate(e_real_pairs):
            for cidx, (p, q) in enumerate(e_real_pairs):
                # 0.25 comes from the fact that we estimate 0.5 (i^ j + j^ i)
                even_cov_mat[ridx, cidx] = 0.25 * (
                    cov_func(i, j, p, q) + cov_func(i, j, q, p) +
                    cov_func(j, i, p, q) + cov_func(j, i, q, p))

        w, _ = np.linalg.eigh(even_cov_mat)
        if not np.all(w >= 0):
            raise ValueError(
                "covariance matrix for xy_even:{} not postiive semidefinite".
                format(circuit_idx))

        variance_dict['xy_even'][circuit_idx] = even_cov_mat / num_samples

        odd_cov_mat = np.zeros((len(o_real_pairs), len(o_real_pairs)),
                               dtype=float)
        for ridx, (i, j) in enumerate(o_real_pairs):
            for cidx, (p, q) in enumerate(o_real_pairs):
                odd_cov_mat[ridx, cidx] = 0.25 * (
                    cov_func(i, j, p, q) + cov_func(i, j, q, p) +
                    cov_func(j, i, p, q) + cov_func(j, i, q, p))
        w, _ = np.linalg.eigh(odd_cov_mat)
        if not np.all(w >= 0):
            raise ValueError(
                "covariance matrix for xy_odd:{} not postiive semidefinite".
                format(circuit_idx))

        variance_dict['xy_odd'][circuit_idx] = odd_cov_mat / num_samples

        if circuit_idx == 0:
            z_cov_mat = np.zeros((num_qubits, num_qubits), dtype=float)
            for ridx, i in enumerate(range(num_qubits)):
                for cidx, p in enumerate(range(num_qubits)):
                    z_cov_mat[ridx, cidx] = cov_func(i, i, p, p)
            w, _ = np.linalg.eigh(z_cov_mat)
            if not np.all(w >= -1.0E-15):
                raise ValueError(
                    "covariance matrix for z:{} not postiive semidefinite".
                    format(circuit_idx))

            variance_dict['z'][circuit_idx] = z_cov_mat / num_samples

    return variance_dict


def resample_opdm(opdm: np.ndarray,
                  var_dict: Dict) -> np.ndarray:  # testpragma: no cover
    """Resample an 1-RDM assuming Gaussian statistics.

    Args:
        opdm: mean-values
        var_dict: dictionary of covariances indexed by circuit and permutation
        fixed_trace_psd_projection: Boolean for if fixed trace positive
            projection should be applied
    """
    num_qubits = opdm.shape[0]
    qubit_permutations = ccu.generate_permutations(num_qubits)
    new_opdm = np.zeros_like(opdm)
    for circuit_idx, permutation in enumerate(qubit_permutations):
        e_real_pairs = [
            permutation[idx:idx + 2] for idx in np.arange(0, num_qubits - 1, 2)
        ]
        o_real_pairs = [
            permutation[idx:idx + 2] for idx in np.arange(1, num_qubits - 1, 2)
        ]

        # get all the even and odd pairs
        even_means = [opdm[pp[0], pp[1]] for pp in e_real_pairs]
        opdm_terms = np.random.multivariate_normal(
            mean=even_means, cov=var_dict['xy_even'][circuit_idx])
        for idx, (pp0, pp1) in enumerate(e_real_pairs):
            new_opdm[pp0, pp1] = opdm_terms[idx]
            new_opdm[pp1, pp0] = opdm_terms[idx]

        odd_means = [opdm[pp[0], pp[1]] for pp in o_real_pairs]
        opdm_terms = np.random.multivariate_normal(
            mean=odd_means, cov=var_dict['xy_odd'][circuit_idx])
        for idx, (pp0, pp1) in enumerate(o_real_pairs):
            new_opdm[pp0, pp1] = opdm_terms[idx]
            new_opdm[pp1, pp0] = opdm_terms[idx]

        if circuit_idx == 0:
            # resample_diagonal_terms
            opdm_diagonal = np.random.multivariate_normal(
                mean=np.diagonal(opdm), cov=var_dict['z'][circuit_idx])

            # because fill_diagonal documentat seems out of date.
            new_opdm[np.diag_indices_from(new_opdm)] = opdm_diagonal

    return new_opdm


def energy_from_opdm(opdm, constant, one_body_tensor, two_body_tensor):
    """Evaluate the energy of an opdm assuming the 2-RDM is opdm ^ opdm.

    Args:
        opdm: single spin-component of the full spin-orbital opdm.
        constant: constant shift to the Hamiltonian. Commonly this is the
            nuclear repulsion energy.
        one_body_tensor: spatial one-body integrals
        two_body_tensor: spatial two-body integrals
    """
    spin_opdm = np.kron(opdm, np.eye(2))
    spin_tpdm = 2 * wedge(spin_opdm, spin_opdm, (1, 1), (1, 1))
    molecular_hamiltonian = generate_hamiltonian(
        constant=constant,
        one_body_integrals=one_body_tensor,
        two_body_integrals=two_body_tensor)
    rdms = InteractionRDM(spin_opdm, spin_tpdm)
    return rdms.expectation(molecular_hamiltonian).real


def mcweeny_purification(rho: np.ndarray,
                         threshold: Optional[float] = 1e-8) -> np.ndarray:
    """Implementation of McWeeny purification

    Args:
        rho: density matrix to purify.
        threshold: stop when ||P**2 - P|| falls below this value.

    Returns:
        Purified density matrix.
    """
    error = np.inf
    new_rho = rho.copy()
    while error > threshold:
        new_rho = 3 * (new_rho @ new_rho) - 2 * (new_rho @ new_rho @ new_rho)
        error = np.linalg.norm(new_rho @ new_rho - new_rho)
    return new_rho


def fidelity_witness(target_unitary: np.ndarray, omega: List[int],
                     measured_opdm: np.ndarray) -> float:
    """Calculate the fidelity witness.

    The fidelity witness is a lower bound to the true fidelity.

    Args:
        target_unitary: unitary representing the intended basis change.
        omega: List of integers corresponding to the initial computational
            basis state.
        measured_opdm: opdm to build the witness from.

    Returns:
        Fidelity witness, a floating point number less than 1. This can be
            negative!
    """
    undone_opdm = target_unitary.conj().T @ measured_opdm @ target_unitary
    fidelity_witness_val = 1
    for i in range(measured_opdm.shape[0]):
        fidelity_witness_val -= undone_opdm[i, i] + omega[i] - 2 * omega[i] * \
                                undone_opdm[i, i]
    return fidelity_witness_val


def fidelity(target_unitary: np.ndarray, measured_opdm: np.ndarray) -> float:
    """Computes the fidelity between an idempotent 1-RDM and the target unitary.

    Args:
        target_unitary: unitary representing basis transformation.
        measured_opdm: purified opdm.
    """
    w, v = np.linalg.eigh(measured_opdm)
    eig_of_one_idx = np.where(np.isclose(w, 1))[0]
    occupied_eigvects = v[:, eig_of_one_idx]
    return abs(
        np.linalg.det(target_unitary[:, :len(eig_of_one_idx)].conj().T
                      @ occupied_eigvects))**2
