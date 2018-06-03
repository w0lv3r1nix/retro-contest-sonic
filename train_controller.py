import click
import pickle
import os
import numpy as np
from const import *
import torch.multiprocessing as multiprocessing
from torch.multiprocessing import Queue
import time
from models.helper import save_checkpoint
from lib.agent_play import VAECGame
from lib.train_utils import init_models
from models.controller import Controller


def rankmin(x):
    u, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    csum = np.zeros_like(counts)
    csum[1:] = counts[:-1].cumsum()
    return csum[inv]


def train_controller(current_time):
    current_time = str(current_time)
    number_generations = 0
    games = GAMES
    levels = LEVELS
    max_timesteps = MAX_TIMESTEPS
    result_queue = Queue()

    vae, lstm, best_controller, solver, checkpoint = init_models(current_time)
    if checkpoint:
        current_ctrl_version = checkpoint["version"]
        current_solver_version = checkpoint["solver_version"]
        new_results = solver.result()
        current_best = new_results[1]
    else:
        current_ctrl_version = 0
        current_solver_version = 0
        current_best = 0

    game = games[0]
    games.remove(game)
    level = levels[game][0]
    levels[game].remove(level)

    while True:
        solutions = solver.ask()
        fitlist = np.zeros(POPULATION)
        left = 0
    
        if current_best > SCORE_CAP:
            if len(levels[game]) == 0:
                game = game[0]
                games.remove(game)
                current_best = 0
            level = levels[game][0]
            levels[game].remove(level)

        print("[CONTROLLER] Current level is: %s" % level)
        while left < POPULATION:
            jobs = []
            todo = PARALLEL if left + PARALLEL <= POPULATION else (left + PARALLEL) % left
            print("[CONTROLLER] Starting new batch")
            for job in range(todo):
                idx = left + job
                controller = Controller(LATENT_VEC, PARAMS_FC1, ACTION_SPACE).to(DEVICE)
                new_w1 = torch.tensor(solutions[idx][0:PARAMS_FC1 + LATENT_VEC],\
                                                dtype=torch.float, device=DEVICE)
                new_w2 = torch.tensor(solutions[idx][PARAMS_FC1 + LATENT_VEC:],\
                                                    dtype=torch.float, device=DEVICE)
                controller.state_dict()['fc1.weight'].data.copy_(new_w1)
                controller.state_dict()['fc2.weight'].data.copy_(new_w2)
                new_game = VAECGame(current_time, idx, vae, lstm, controller, \
                        game, level, result_queue, max_timesteps)
                jobs.append(new_game)
            for p in jobs:
                p.start()
            for p in jobs:
                p.join()
            left = left + PARALLEL
            print("[CONTROLLER] Done with batch")

        times = []
        for i in range(POPULATION):
            result = result_queue.get()
            keys = list(result.keys())
            result = list(result.values())
            fitlist[keys[0]] = result[0][0]
            times.append(result[0][1])

        current_score = np.max(fitlist)
        average_score = np.mean(fitlist)
        max_idx = np.argmax(fitlist)
        fitlist = (rankmin(fitlist)) / POPULATION
        solver.tell(fitlist)
        new_results = solver.result()

        print("[CONTROLLER] Total duration for generation: %.3f seconds, average duration:"
            " %.3f seconds per process, %.3f seconds per run" % ((np.sum(times), \
                    np.mean(times), np.mean(times) / REPEAT_ROLLOUT)))
        print("[CONTROLLER] Creating generation: {} ...".format(current_solver_version + 1))
        print("[CONTROLLER] Current best score: {}, new run best score: {}".format(current_best, current_score))
        print("[CONTROLLER] Best score ever: {}, current number of improvements: {}\n".format(current_best, current_ctrl_version))
        print("[CONTROLLER] Average score on all of the processes: {}".format(average_score))
    
        number_generations += 1
        current_solver_version += 1
        if number_generations % TIMESTEP_DECAY_TICK == 0:
            max_timesteps += TIMESTEP_DECAY
    
        if current_score > current_best:
            current_ctrl_version += 1
            new_w1 = torch.tensor(solutions[max_idx][0:PARAMS_FC1 + LATENT_VEC], dtype=torch.float, device=DEVICE)
            new_w2 = torch.tensor(solutions[max_idx][PARAMS_FC1 + LATENT_VEC:], dtype=torch.float, device=DEVICE)
            best_controller.state_dict()['fc1.weight'].data.copy_(new_w1)
            best_controller.state_dict()['fc2.weight'].data.copy_(new_w2)
            state = { 'version': current_ctrl_version,
                      'solver_version': current_solver_version,
                      'score': current_score,
                      'level': level}
            save_checkpoint(best_controller, "controller", state, current_time)
            current_best = current_score

        dir_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), \
                    'saved_models', current_time, "{}-solver.pkl".format(current_solver_version))
        pickle.dump(solver, open(dir_path, 'wb'))

        
@click.command()
@click.option("--folder", default=-1)
def main(folder):
    multiprocessing.set_start_method('spawn')
    if folder == -1:
        current_time = int(time.time())
    else:
        current_time = folder
    train_controller(current_time)
        


if __name__ == "__main__":
    main()